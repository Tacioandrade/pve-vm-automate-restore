import argparse
import datetime
import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from pbs_restore.config import load_config
from pbs_restore.proxmox_client import (
    get_proxmox_client, get_latest_backup, wait_for_task, 
    check_vm_exists, get_vm_name_from_system, check_connection
)
from pbs_restore.telegram import send_telegram_message
from pbs_restore.screenshot import wait_and_screenshot
from pbs_restore.vm_manager import get_vms_to_restore, rotate_vm
from pbs_restore.report import RestorationContext
from pbs_restore.utils import cleanup_old_artifacts
from pbs_restore.exceptions import PBSRestoreError, ConfigurationError, VMFileError, BackupNotFoundError
from pbs_restore.models import AppConfig, RestorationResult
from pbs_restore.constants import DEFAULT_VMS_FILE
from pbs_restore.logging_config import setup_logging

logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser(
        description="Ferramenta de Automação de Restauração para Proxmox Backup Server (PBS)",
        add_help=True
    )
    parser.add_argument("-c", "--count", type=int, help="Número de VMs a restaurar simultaneamente (sobrescreve o .env)")
    parser.add_argument("-v", "--vms", type=str, help="Caminho para o arquivo de lista de VMs (ex: vms.txt)")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Ativa o modo de simulação: não executa ações reais no Proxmox")
    parser.add_argument("-k", "--no-ia", action="store_true", help="Pula a etapa de análise visual com a IA Gemini")
    parser.add_argument("-t", "--no-telegram", action="store_true", help="Desativa o envio do relatório final para o Telegram")
    return parser.parse_args()


def process_vm(vmid, config, proxmox, context, vms_file, args):
    """Executa o fluxo completo para uma única VM."""
    node = config.proxmox_node
    backup_storage = config.backup_storage
    restore_storage = config.restore_storage
    
    # Create initial result object
    res = RestorationResult(vmid=vmid)
    context.add_result(res)
    
    try:
        logger.info(f"--- [Thread {vmid}] Iniciando processamento ---")
        
        try:
            backup = get_latest_backup(proxmox, node, backup_storage, vmid)
            res.backup_volid = backup['volid']
        except BackupNotFoundError:
            logger.error(f"❌ [Thread {vmid}] Nenhum backup encontrado no storage '{backup_storage}'.")
            res.status = "❌ Sem backup"
            rotate_vm(vms_file, vmid)
            return
            
        volid = res.backup_volid
        
        # Extrair data do nome do backup
        backup_date_str = ""
        backup_dt = None
        
        # Tenta extrair formato PBS (ISO 8601)
        if 'Z' in volid and 'T' in volid:
            try:
                date_part = volid.split('/')[-1].replace('Z', '')
                backup_dt = datetime.datetime.fromisoformat(date_part)
                backup_date_str = backup_dt.strftime('%d/%m/%Y %H:%M:%S')
            except (ValueError, IndexError):
                pass
        # Tenta extrair formato VZDump (YYYY_MM_DD-HH_MM_SS)
        elif 'vzdump-' in volid:
            try:
                import re
                match = re.search(r'(\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2})', volid)
                if match:
                    date_part = match.group(1)
                    backup_dt = datetime.datetime.strptime(date_part, '%Y_%m_%d-%H_%M_%S')
                    backup_date_str = backup_dt.strftime('%d/%m/%Y %H:%M:%S')
            except (ValueError, IndexError):
                pass

        alert_msg = ""
        if backup_dt:
            days_ago = (datetime.datetime.now() - backup_dt).days
            if days_ago > 15:
                alert_msg = f"⚠️ O backup restaurado tem mais de 15 dias: {backup_date_str}"
                logger.warning(f"[Thread {vmid}] {alert_msg}")

        res.alert_msg = alert_msg
        is_container = "/ct/" in volid.lower() or "lxc" in volid.lower()
        
        if not args.dry_run and check_vm_exists(proxmox, node, vmid):
            logger.error(f"❌ [Thread {vmid}] Restauração cancelada: VMID ativo no nó.")
            res.status = "🦘 Ignorada (ID em uso)"
            rotate_vm(vms_file, vmid)
            return
            
        logger.info(f"[Thread {vmid}] Disparando restauração ({'LXC' if is_container else 'VM'})...")
        
        start_dt = datetime.datetime.now()
        res.start_time = start_dt
        
        if args.dry_run:
            logger.info(f"[Thread {vmid}] [DRY-RUN] Simulando restauração...")
            time.sleep(2)
            exitstatus = 'OK'
        else:
            if is_container:
                response = proxmox.nodes(node).lxc.post(
                    vmid=vmid, ostemplate=volid, storage=restore_storage, restore=1
                )
            else:
                response = proxmox.nodes(node).qemu.post(
                    vmid=vmid, archive=volid, storage=restore_storage
                )
            upid = response
            logger.info(f"[Thread {vmid}] Restore iniciado. UPID: {upid}")
            exitstatus = wait_for_task(proxmox, node, upid)
            
        end_dt = datetime.datetime.now()
        res.end_time = end_dt
        
        total_seconds = int((end_dt - start_dt).total_seconds())
        m, s = divmod(total_seconds, 60)
        h, m = divmod(m, 60)
        res.duration = f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s" if m > 0 else f"{s}s"
        
        if exitstatus == 'OK':
            if args.dry_run:
                res.vm_name = "VM-Simulada"
            else:
                res.vm_name = get_vm_name_from_system(proxmox, node, vmid, is_container)
                
            logger.info(f"✅ [Thread {vmid}] Restauração concluída.")
            res.status = "✅ Sucesso"
            
            # Boot e Screenshot
            if config.auto_start_vm and not args.dry_run:
                try:
                    logger.info(f"[Thread {vmid}] Iniciando para verificação...")
                    if is_container:
                        proxmox.nodes(node).lxc(vmid).status.start.post()
                    else:
                        proxmox.nodes(node).qemu(vmid).status.start.post()
                    
                    res.ia_status = "Aguardando análise"
                    # Chamada síncrona dentro da thread VM, pois cada VM já está em sua thread
                    wait_and_screenshot(config, vmid, is_container, context)
                except Exception as e:
                    logger.error(f"[Thread {vmid}] Erro no boot: {e}")
            elif args.dry_run:
                res.ia_status = "Pulado (Dry-run)"
            else:
                res.ia_status = "Desativado"
        else:
            logger.error(f"❌ [Thread {vmid}] Falha: {exitstatus}")
            res.status = f"❌ Falha ({exitstatus})"
            
        rotate_vm(vms_file, vmid)

    except Exception as e:
        logger.exception(f"❌ [Thread {vmid}] Erro crítico: {e}")
        res.status = "❌ Erro interno"
        res.error_details = str(e)
        rotate_vm(vms_file, vmid)

def main():
    args = parse_args()
    
    try:
        config = load_config()
        if args.count:
            config.vm_restore_count = args.count
        if args.no_ia:
            config.analyze_with_gemini = False
            
        setup_logging(config.log_level)
    except ConfigurationError as e:
        print(f"Erro de configuração: {e}")
        return
    except Exception as e:
        print(f"Erro inesperado no load_config: {e}")
        return

    node = config.proxmox_node
    
    logger.info("Conectando na API do Proxmox...")
    try:
        proxmox = get_proxmox_client(config)
        check_connection(proxmox)
    except Exception as e:
        logger.error(f"❌ Falha crítica na conexão Proxmox: {e}")
        return

    try:
        vms_file = args.vms if args.vms else DEFAULT_VMS_FILE
        vms_to_restore = get_vms_to_restore(vms_file, config.vm_restore_count)
    except VMFileError as e:
        logger.error(f"Erro no arquivo de VMs: {e}")
        return
    except Exception as e:
        logger.error(f"Erro ao processar lista: {e}")
        return

    if not vms_to_restore:
        logger.info("Nenhuma VM para restaurar.")
        return

    logger.info(f"🚀 Iniciando {len(vms_to_restore)} restaurações (Máx Workers: {config.max_workers})...")
    context = RestorationContext()
    
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        for vmid in vms_to_restore:
            executor.submit(process_vm, vmid, config, proxmox, context, vms_file, args)

    # Relatório final
    try:
        logger.info("Gerando relatório final...")
        total = len(vms_to_restore)
        sucesso = falha = ignorada = sem_backup = 0
        detail_lines = []
        
        # Manter ordem original do arquivo
        for vmid in vms_to_restore:
            res = context.get_result(vmid)
            if not res: continue
            
            line = f"────────────────\n"
            if "Ignorada" in res.status:
                ignorada += 1
                line += f"🦘 <b>VMID: {vmid}</b> - Ignorada\nMotivo: ID em uso\n"
            elif "Sem backup" in res.status:
                sem_backup += 1
                line += f"⚠️ <b>VMID: {vmid}</b> - Sem backup!\n"
            else:
                icon = "✅" if "Sucesso" in res.status else "❌"
                if icon == "✅": sucesso += 1
                else: falha += 1
                
                line += f"{icon} <b>VMID: {vmid}</b> - {res.vm_name}\n"
                line += f"Tempo: <code>{res.duration}</code>\n"
                if config.auto_start_vm and config.analyze_with_gemini:
                    ia_text = f"<b>{res.ia_status}</b>" if "Erro" in res.ia_status or "Falha" in res.ia_status else res.ia_status
                    line += f"Análise IA: {ia_text}\n"
                if res.alert_msg:
                    line += f"Aviso: {res.alert_msg}\n"
            detail_lines.append(line)
            logger.info(line.replace("<b>","").replace("</b>","").replace("<code>","").replace("</code>","").replace("────────────────",""))

        summary = f"📊 <b>Resumo da Restauração</b>\n"
        if args.dry_run: summary = f"⚠️ <b>[MODO SIMULAÇÃO - DRY RUN]</b>\n" + summary
        summary += f"Total: {total} | Sucesso: {sucesso} | Falha: {falha}\n"
        summary += f"Ignorada: {ignorada} | Sem Backup: {sem_backup}\n\n"
        
        final_report = summary + "".join(detail_lines) + "\n🤖 <b>Finalizado.</b>"
        
        if not args.no_telegram:
            send_telegram_message(config.telegram_bot_token, config.telegram_chat_id, final_report)
        else:
            logger.info("Envio de telegram pulado.")
            
    except Exception as e:
        logger.exception(f"Erro no relatório final: {e}")

    cleanup_old_artifacts("prints", days=7)
    logger.info("Processo finalizado.")

if __name__ == "__main__":
    main()
