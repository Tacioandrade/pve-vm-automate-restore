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
import json
from google import genai

# Lock global para atualização segura do dicionário de resultados
results_lock = threading.Lock()
# Dicionário global para armazenar os dados de cada VM restaurada
# No formato: { vmid: { 'status_msg': ..., 'duration_str': ..., 'ia_status': ..., 'alert_msg': ... } }
restoration_results = {}

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
    
    # Opcionais dependendo do ANALYZE_WITH_GEMINI
    analyze_with_gemini = os.getenv("ANALYZE_WITH_GEMINI", "False").lower() == "true"
    if analyze_with_gemini:
        required.append("GEMINI_API_KEY")
        required.append("GEMINI_MODEL")

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
    config["ANALYZE_WITH_GEMINI"] = analyze_with_gemini
        
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

# Envia para o Gemini a imagem para checar se o SO está ok
def analyze_image_with_gemini(image_path, config):
    """Envia a imagem para o Google Gemini API tentando diferentes modelos de fallback."""
    try:
        # Lista de modelos para tentar (do mais provável para o menos provável)
        models_to_try = [
            config.get("GEMINI_MODEL", "gemini-1.5-flash"),
            "gemini-1.5-flash",
            "gemini-flash-latest",
            "gemini-1.5-flash-001"
        ]
        
        # Remove duplicatas mantendo a ordem
        models_to_try = list(dict.fromkeys(models_to_try))
        
        client = genai.Client(api_key=config["GEMINI_API_KEY"])
        
        prompt = """
Analise a imagem. Responda APENAS com JSON válido, sem explicações:

{
  "boot_concluido": true/false,
  "estado": "login"|"bloqueio"|"area_trabalho"|"app_aberto"|"shell"|"erro"|"atualizando"|"tela_preta"|"indeterminado",
  "os_detectado": "windows"|"linux"|"indeterminado",
  "confianca": "alta"|"media"|"baixa"
}

BOOT_CONCLUIDO=false se houver: logo de fabricante, POST, "BIOS"/"UEFI", GRUB/bootloader, animação de boot, mensagens de kernel ("[OK]", "Starting", "systemd"), tela de atualização ("Não desligue", "Configurando atualizações"), BSOD/tela azul, "Recuperação"/"Reparo Automático"/"WinRE". Caso contrário: true.

ESTADO (use o primeiro que se aplicar):
- erro: BSOD, tela azul, "Reparo Automático", "Automatic Repair", "Recovery"
- atualizando: "Não desligue", "Configurando atualizações X%", "Windows Update"
- tela_preta: imagem preta ou sem conteúdo identificável
- login: campo de senha, lista de usuários, "Ctrl+Alt+Delete"/"Pressione Ctrl+Alt+Delete", "Sign in", prompt TTY ("login:", "Password:"), tela de login gráfico Linux
- bloqueio: relógio/data em destaque, sem campo de senha visível
- shell: prompt CMD ("C:\\"), PowerShell ("PS C:\\"), bash/zsh ("$","#","user@host")
- area_trabalho: ícones, barra de tarefas/dock, papel de parede sem login/bloqueio
- app_aberto: janela de aplicativo em primeiro plano
- indeterminado: nenhuma categoria aplicável

OS_DETECTADO:
- windows: logo Windows, barra de tarefas com Iniciar, CMD/PowerShell, interface Fluent
- linux: prompt shell Linux, logo de distro, GRUB, GNOME/KDE/XFCE
- indeterminado: não identificável

CONFIANCA: alta=elementos claros / media=parcialmente visíveis / baixa=borrado, recortado ou contraditório
        """
        
        img = Image.open(image_path)
        
        for model_name in models_to_try:
            try:
                print(f"DEBUG: [Thread Screenshot] Tentando modelo {model_name} para {image_path}...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=[prompt, img]
                )
                
                # Tenta extrair o JSON da resposta
                text = response.text.strip()
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                
                data = json.loads(text)
                print(f"DEBUG: [Thread Screenshot] Sucesso com o modelo {model_name}. Resultado: {data}")
                return data
            except Exception as e:
                print(f"DEBUG: [Thread Screenshot] Modelo {model_name} falhou: {e}")
                continue
        
        return None
        
    except Exception as e:
        print(f"DEBUG: [Thread Screenshot] Falha geral na análise com Gemini: {e}")
        return None

def wait_and_screenshot(config, vmid, is_container):
    """Aguarda o tempo configurado e captura uma screenshot da VM restaurada."""
    
    # Se for container, não tentamos tirar print pois o Proxmox/QEMU não suporta
    if is_container:
        print(f"DEBUG: [Thread Screenshot] Ignorando captura de tela para VMID {vmid} (Container LXC não suportado).")
        with results_lock:
            if vmid in restoration_results:
                restoration_results[vmid]['ia_status'] = "⚠️ N/A (LXC Container)"
        return

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
            
            # --- Análise via IA ---
            ia_label = "Falta análise"
            if config["ANALYZE_WITH_GEMINI"]:
                ia_data = analyze_image_with_gemini(local_jpg, config)
                if ia_data:
                    if ia_data.get("boot_concluido") is True:
                        ia_label = "✅ OK"
                    else:
                        ia_label = f"❌ Erro detectado ({ia_data.get('estado')})"
                else:
                    ia_label = "⚠️ Falha na análise IA"
                
                # Atualiza o dicionário global
                with results_lock:
                    if vmid in restoration_results:
                        restoration_results[vmid]['ia_status'] = ia_label
        
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
                        t = threading.Thread(target=wait_and_screenshot, args=(config, vmid, is_container), daemon=False)
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
            
        with results_lock:
            restoration_results[vmid] = {
                'status_msg': status_msg,
                'vmid': vmid,
                'vm_name': vm_name,
                'duration_str': duration_str,
                'alert_msg': alert_msg,
                'ia_status': "Não verificado" if not config["AUTO_START_VM"] else "Aguardando análise"
            }
        
        # Rotaciona a VM para o final do arquivo vms.txt (Round-Robin)
        print(f"Rotacionando VM {vmid} para o fim da lista (Round-Robin)...")
        rotate_vm("vms.txt", vmid)

    # Aguarda todas as screenshots terminarem antes de enviar o Telegram
    if screenshot_threads:
        print("\n--- Finalizando capturas de tela agendadas ---")
        for t in screenshot_threads:
            t.join()

    # Monta o relatório consolidado
    print("\n--- Gerando relatório final ---")
    for vmid in vms_to_restore:
        with results_lock:
            res = restoration_results.get(vmid)
        if not res: continue
        
        line = f"############################\n\n"
        # Ajuste de ícones baseado no status
        if "Ignorada" in res['status_msg']:
            line += f"🦘 VMID: <code>{res['vmid']}</code> - Ignorada\n<b>Motivo:</b> ID já em uso\n\n"
        elif "Ausente" in res['status_msg']:
             line += f"❌ Ausente - VMID: <code>{res['vmid']}</code> - <b>Status:</b> Sem backup!\n\n"
        else:
            line += f"{res['status_msg']} - VMID: <code>{res['vmid']}</code> - <b>Nome:</b> {res['vm_name']}\n"
            if res['duration_str']:
                line += f"Tempo de restauração: {res['duration_str']}\n"
                
            if config["AUTO_START_VM"] and config["ANALYZE_WITH_GEMINI"]:
                line += f"Status segundo IA: {res['ia_status']}\n"
            
            if res['alert_msg']:
                line += f"{res['alert_msg']}\n"
            line += "\n"
        
        report_lines.append(line)
        # Exibe no console também
        print(line.replace("<b>","").replace("</b>","").replace("<code>","").replace("</code>",""))

    report_lines.append("\n🤖 <b>Por favor, verifique no Proxmox se as máquinas foram restauradas corretamente.</b>")
    final_report = "".join(report_lines)
    
    print("\nDisparando relatório no Telegram...")
    send_telegram_message(config["TELEGRAM_BOT_TOKEN"], config["TELEGRAM_CHAT_ID"], final_report)
    print("Processo finalizado.")

if __name__ == "__main__":
    main()
