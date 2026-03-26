import os
import time
import datetime
import requests
import urllib3
from dotenv import load_dotenv
from proxmoxer import ProxmoxAPI
from PIL import Image
import threading
import subprocess

# Disable insecure request warnings when verify_ssl=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def load_config():
    load_dotenv()
    required = [
        "PROXMOX_URL", "PROXMOX_NODE", "PROXMOX_USER", "PROXMOX_PASSWORD", 
        "BACKUP_STORAGE", "RESTORE_STORAGE", "VM_RESTORE_COUNT",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SCREENSHOT_WAIT_MINUTES",
        "PROXMOX_TIMEOUT", "AUTO_START_VM"
    ]
    config = {}
    for req in required:
        val = os.getenv(req)
        if not val:
            raise ValueError(f"Faltando variável paramétrica no .env: {req}")
        config[req] = val
        
    try:
        config["VM_RESTORE_COUNT"] = int(config["VM_RESTORE_COUNT"])
    except ValueError:
        raise ValueError("O parâmetro VM_RESTORE_COUNT precisa ser um número inteiro")
        
    try:
        config["SCREENSHOT_WAIT_MINUTES"] = int(config["SCREENSHOT_WAIT_MINUTES"])
    except ValueError:
        raise ValueError("O parâmetro SCREENSHOT_WAIT_MINUTES precisa ser um número inteiro")
        
    try:
        config["PROXMOX_TIMEOUT"] = int(config.get("PROXMOX_TIMEOUT", 60))
    except ValueError:
        raise ValueError("O parâmetro PROXMOX_TIMEOUT precisa ser um número inteiro")
        
    config["AUTO_START_VM"] = os.getenv("AUTO_START_VM", "True").lower() == "true"
        
    return config

def get_vms_to_restore(filename, count):
    if not os.path.exists(filename):
        raise FileNotFoundError(f"Arquivo não encontrado: {filename}")
        
    with open(filename, 'r') as f:
        # Read lines, strip whitespace, remove empty lines
        lines = [line.strip() for line in f if line.strip()]
        
    return lines[:count]

def send_telegram_message(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Falha ao enviar mensagem no Telegram: {e}")
        if e.response is not None:
            print(f"Resposta da API do Telegram: {e.response.text}")

def get_latest_backup(proxmox, node, storage, vmid):
    try:
        # Fetch contents of the backup storage
        content = proxmox.nodes(node).storage(storage).content.get()
        
        # Filter items for backps matching this VMID
        # Format usually looks like: backup/vzdump-qemu-100-2023_10_01-12_00_00.vma.zst
        # or pbs:backup/vm/100/...
        backups = [
            item for item in content 
            if item.get('content') == 'backup' and (f"/{vmid}/" in item.get('volid', '') or f"-{vmid}-" in item.get('volid', ''))
        ]
        
        if not backups:
            return None
            
        # Sort backups by creation time (ctime) descending (newest first)
        backups.sort(key=lambda x: x.get('ctime', 0), reverse=True)
        return backups[0]
        
    except Exception as e:
        print(f"Erro ao procurar backup da VM {vmid}: {e}")
        return None

def wait_for_task(proxmox, node, upid):
    """Polls the Proxmox task status until it completes."""
    while True:
        try:
            task = proxmox.nodes(node).tasks(upid).status.get()
            if task.get('status') == 'stopped':
                return task.get('exitstatus', 'Desconhecido')
        except Exception as e:
            print(f"Erro ao checar status da tarefa {upid}: {e}")
            return "Erro"
        time.sleep(5)

def get_vm_name_from_system(proxmox, node, vmid, is_container):
    name = "Desconhecido"
    
    # Lendo o arquivo diretamente do host PVE
    qemu_path = f"/etc/pve/nodes/{node}/qemu-server/{vmid}.conf"
    lxc_path = f"/etc/pve/nodes/{node}/lxc/{vmid}.conf"
    path = lxc_path if is_container else qemu_path
    name_key = "hostname:" if is_container else "name:"
    
    # Funciona caso o script esteja rodando no próprio servidor Proxmox
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                for line in f:
                    if line.strip().startswith(name_key):
                        return line.split(':', 1)[1].strip()
        except Exception:
            pass
            
    # Funciona caso o script esteja no seu notebook rodando remotamente
    try:
        if is_container:
            conf = proxmox.nodes(node).lxc(vmid).config.get()
            return conf.get('hostname', name)
        else:
            conf = proxmox.nodes(node).qemu(vmid).config.get()
            return conf.get('name', name)
    except Exception:
        pass
        
    return name

def check_vm_exists(proxmox, node, vmid):
    try:
        # Check QEMU
        qemu_vms = proxmox.nodes(node).qemu.get()
        for vm in qemu_vms:
            if str(vm.get('vmid')) == str(vmid):
                return True
                
        # Check LXC
        lxc_vms = proxmox.nodes(node).lxc.get()
        for vm in lxc_vms:
            if str(vm.get('vmid')) == str(vmid):
                return True
    except Exception as e:
        print(f"Erro ao verificar VMs existentes: {e}")
        # Retorna True em caso de falha de conexão na checagem pra evitar qualquer desastre
        return True
        
    return False

def rotate_vm(filename, vmid):
    if not os.path.exists(filename):
        return
    with open(filename, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]
        
    vmid_str = str(vmid)
    if vmid_str in lines:
        lines.remove(vmid_str)
        lines.append(vmid_str)
        with open(filename, 'w') as f:
            for line in lines:
                f.write(line + '\n')

def get_proxmox_client(config):
    # Determine host and port from URL
    url = config["PROXMOX_URL"]
    url = url.replace("https://", "").replace("http://", "")
    
    parts = url.split(":")
    host = parts[0]
    port = 8006
    if len(parts) > 1:
        try:
            port = int(parts[1].split("/")[0])
        except ValueError:
            pass
            
    return ProxmoxAPI(
        host,
        port=port,
        user=config["PROXMOX_USER"],
        password=config["PROXMOX_PASSWORD"],
        verify_ssl=False,
        timeout=config["PROXMOX_TIMEOUT"]
    )

def wait_and_screenshot(config, vmid):
    """Aguarda o tempo configurado e captura uma screenshot da VM restaurada."""
    wait_time = config["SCREENSHOT_WAIT_MINUTES"] * 60
    print(f"DEBUG: [Thread Screenshot] Aguardando {config['SCREENSHOT_WAIT_MINUTES']} minutos para capturar screenshot da VM {vmid}...")
    time.sleep(wait_time)
    
    node = config["PROXMOX_NODE"]
    proxmox_host = config["PROXMOX_URL"].replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
    
    output_dir = "prints"
    os.makedirs(output_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    remote_ppm = f"/tmp/screenshot_{vmid}_{timestamp}.ppm"
    local_ppm = os.path.join(output_dir, f"{vmid}_{timestamp}.ppm")
    local_jpg = os.path.join(output_dir, f"{vmid}_{timestamp}.jpg")

    print(f"DEBUG: [Thread Screenshot] Capturando screenshot da VM {vmid}...")

    try:
        # Re-conecta para garantir que a sessão esteja ativa na thread
        proxmox = get_proxmox_client(config)
        
        # --- Passo 1: envia screendump ao monitor QEMU ---
        proxmox.nodes(node).qemu(vmid).monitor.post(command=f"screendump {remote_ppm}")
        print(f"DEBUG: [Thread Screenshot] Screendump gerado no host: {remote_ppm}")
        time.sleep(1)

        # --- Passo 2: baixa o PPM via SCP ---
        print(f"DEBUG: [Thread Screenshot] Baixando arquivo do host Proxmox via SCP...")
        scp = subprocess.run(
            ["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             f"root@{proxmox_host}:{remote_ppm}", local_ppm],
            capture_output=True, text=True
        )
        
        if scp.returncode != 0:
            print(f"DEBUG: [Thread Screenshot] Falha no SCP: {scp.stderr.strip()}")
            return

        # --- Passo 3: converte PPM -> JPG com Pillow ---
        if os.path.exists(local_ppm):
            img = Image.open(local_ppm).convert("RGB")
            img.save(local_jpg, "JPEG", quality=90, optimize=True)
            img.close()
            os.remove(local_ppm)
            print(f"DEBUG: [Thread Screenshot] Screenshot salva com sucesso: {local_jpg}")
        
        # --- Passo 4: remove o PPM temporário do servidor Proxmox ---
        subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             f"root@{proxmox_host}", f"rm -f {remote_ppm}"],
            capture_output=True
        )
        print(f"DEBUG: [Thread Screenshot] Arquivo temporário removido do host Proxmox.")

    except Exception as e:
        print(f"DEBUG: [Thread Screenshot] Exceção ao capturar screenshot da VM {vmid}: {e}")

def main():
    try:
        config = load_config()
    except Exception as e:
        print(f"Erro de configuração: {e}")
        return

    node = config["PROXMOX_NODE"]
    backup_storage = config["BACKUP_STORAGE"]
    restore_storage = config["RESTORE_STORAGE"]
    
    print("Conectando na API do Proxmox...")
    try:
        proxmox = get_proxmox_client(config)
        # Test connection
        proxmox.version.get()
        
        # Validação extra: tenta listar as VMs no nó configurado e acessos básicos. 
        # Se falhar (timeout ou erro de permissão), aborta imediatamente.
        print(f"Verificando conectividade e acesso ao nó '{node}'...")
        proxmox.nodes(node).qemu.get()
        
        print(f"Verificando acesso ao storage de backup '{backup_storage}'...")
        proxmox.nodes(node).storage(backup_storage).content.get()
        
    except Exception as e:
        print(f"❌ Falha crítica na conexão ou acesso aos recursos do Proxmox: {e}")
        return

    try:
        vms_to_restore = get_vms_to_restore("vms.txt", config["VM_RESTORE_COUNT"])
    except Exception as e:
        print(f"Erro ao ler arquivo vms.txt: {e}")
        return

    if not vms_to_restore:
        print("Nenhuma VM encontrada para restauração.")
        return

    print(f"Iniciando o processo de restauração para {len(vms_to_restore)} VMs...")
    report_lines = ["<b>Relatório de Restauração do Proxmox</b> \n"]
    screenshot_threads = []
    
    for vmid in vms_to_restore:
        print(f"\n--- Processando VMID: {vmid} ---")
        
        backup = get_latest_backup(proxmox, node, backup_storage, vmid)
        
        if not backup:
            print(f"❌ Nenhum backup encontrado para o VMID {vmid} no storage '{backup_storage}'.")
            print("⚠️ OBS: Por favor, atualize a lista de VMs rodando atualmente no arquivo vms.txt!")
            report_lines.append(f"❌ Ausente - VMID: <code>{vmid}</code> - <b>Status:</b> Sem backup! Atualize o arquivo vms.txt")
            print(f"Rotacionando VM {vmid} para o final do arquivo (Round-Robin)...")
            rotate_vm("vms.txt", vmid)
            continue
            
        volid = backup['volid']
        
        # Extrair data do nome do backup (PBS ou VZDump)
        backup_date_str = ""
        backup_dt = None
        
        # Tenta extrair formato PBS (ISO 8601)
        if 'Z' in volid and 'T' in volid:
            try:
                date_part = volid.split('/')[-1].replace('Z', '')
                backup_dt = datetime.datetime.fromisoformat(date_part)
                backup_date_str = backup_dt.strftime('%d/%m/%Y %H:%M:%S')
            except:
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
            except:
                pass

        alert_msg = ""
        if backup_dt:
            days_ago = (datetime.datetime.now() - backup_dt).days
            if days_ago > 15:
                alert_msg = f"⚠️ <b>O backup restaurado tem mais de 15 dias: {backup_date_str}</b>"
                print(f"\033[93m{alert_msg}\033[0m") # Amarelo no console

        is_container = "/ct/" in volid.lower() or "lxc" in volid.lower()
        type_str = "Container LXC" if is_container else "QEMU VM"
        
        print(f"Último backup encontrado: {volid} ({type_str})")
        
        if check_vm_exists(proxmox, node, vmid):
            print(f"❌ Restauração cancelada: Já existe uma VM/CT com VMID {vmid} ativa no nó '{node}'.")
            report_lines.append(f"🦘 Ignorada - VMID: <code>{vmid}</code> - <b>Motivo:</b> ID já em uso")
            print(f"Rotacionando VM {vmid} para o final do arquivo (Round-Robin)...")
            rotate_vm("vms.txt", vmid)
            continue
            
        print(f"Disparando restauração para o storage: {restore_storage}")
        
        vm_name = "Desconhecido"
        duration_str = ""
        
        try:
            start_dt = datetime.datetime.now()
            print(f"Restauração iniciada: {start_dt.strftime('%d/%m/%Y %H:%M:%S')}")
            
            if is_container:
                response = proxmox.nodes(node).lxc.post(
                    vmid=vmid,
                    ostemplate=volid,
                    storage=restore_storage,
                    restore=1
                )
            else:
                response = proxmox.nodes(node).qemu.post(
                    vmid=vmid,
                    archive=volid,
                    storage=restore_storage
                )
            upid = response
            print(f"Tarefa de restore iniciada. UPID: {upid}")
            
            print("Aguardando conclusão do restore (isso pode demorar minutos)...")
            exitstatus = wait_for_task(proxmox, node, upid)
            
            end_dt = datetime.datetime.now()
            print(f"Restauração finalizada: {end_dt.strftime('%d/%m/%Y %H:%M:%S')}")
            
            total_seconds = int((end_dt - start_dt).total_seconds())
            m, s = divmod(total_seconds, 60)
            h, m = divmod(m, 60)
            if h > 0:
                duration_str = f"{h}h {m}m {s}s"
            elif m > 0:
                duration_str = f"{m}m {s}s"
            else:
                duration_str = f"{s}s"
            print(f"Tempo de restauração: {duration_str}")
            
            if exitstatus == 'OK':
                vm_name = get_vm_name_from_system(proxmox, node, vmid, is_container)
                print(f"✅ Restauração da VM {vmid} - {vm_name} concluída com sucesso.")
                status_msg = "✅ Sucesso"
                
                # Inicia o processo de screenshot se AUTO_START_VM for True
                if config["AUTO_START_VM"]:
                    try:
                        print(f"Iniciando a VM {vmid} para verificação...")
                        if is_container:
                            proxmox.nodes(node).lxc(vmid).status.start.post()
                        else:
                            proxmox.nodes(node).qemu(vmid).status.start.post()
                        
                        # Inicia o processo de screenshot em paralelo
                        t = threading.Thread(target=wait_and_screenshot, args=(config, vmid), daemon=False)
                        t.start()
                        screenshot_threads.append(t)
                    except Exception as e:
                        print(f"Erro ao iniciar a VM {vmid}: {e}")
                else:
                    print(f"A VM {vmid} não foi iniciada (AUTO_START_VM = False). Captura de screenshot ignorada.")
            else:
                print(f"❌ Tarefa de restauração da VM {vmid} falhou com status: {exitstatus}")
                status_msg = f"❌ Falha ({exitstatus})"
                
        except Exception as e:
            print(f"Exceção ocorrida durante restore da VM {vmid}: {e}")
            status_msg = "❌ Erro interno"
            
        if duration_str:
            msg = f"{status_msg} - VMID: <code>{vmid}</code> - <b>Nome:</b> {vm_name}\nTempo de restauração: {duration_str}"
            if alert_msg:
                msg += f"\n{alert_msg}"
            report_lines.append(msg)
        else:
            msg = f"{status_msg} - VMID: <code>{vmid}</code> - <b>Nome:</b> {vm_name}"
            if alert_msg:
                msg += f"\n{alert_msg}"
            report_lines.append(msg)
        
        # Rotaciona a VM para o final do arquivo vms.txt (Round-Robin)
        print(f"Rotacionando VM {vmid} para o fim da lista (Round-Robin)...")
        rotate_vm("vms.txt", vmid)

    # Aguarda todas as screenshots terminarem antes de enviar o Telegram
    if screenshot_threads:
        print("\n--- Finalizando capturas de tela agendadas ---")
        for t in screenshot_threads:
            t.join()

    report_lines.append("\n🤖 <b>Por favor, verifique no Proxmox se as máquinas foram restauradas corretamente.</b>")
    final_report = "\n".join(report_lines)
    
    print("\nDisparando relatório no Telegram...")
    send_telegram_message(config["TELEGRAM_BOT_TOKEN"], config["TELEGRAM_CHAT_ID"], final_report)
    print("Processo finalizado.")

if __name__ == "__main__":
    main()
