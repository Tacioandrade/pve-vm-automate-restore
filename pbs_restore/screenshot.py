import os
import time
import subprocess
import threading
import logging
from PIL import Image
from pbs_restore.proxmox_client import get_proxmox_client
from pbs_restore.gemini import analyze_image_with_gemini

logger = logging.getLogger(__name__)

def wait_and_screenshot(config, vmid, is_container, context):
    """Aguarda o tempo configurado e captura uma screenshot da VM restaurada."""
    
    # Se for container, não tentamos tirar print pois o Proxmox/QEMU não suporta
    if is_container:
        logger.debug(f"Ignorando captura de tela para VMID {vmid} (Container LXC não suportado).")
        context.update_result(vmid, {'ia_status': "⚠️ N/A (LXC Container)"})
        return

    wait_time = config.screenshot_wait_minutes * 60
    logger.info(f"Aguardando {config.screenshot_wait_minutes} minutos para capturar screenshot da VM {vmid}...")
    time.sleep(wait_time)
    
    node = config.proxmox_node
    proxmox_host = config.proxmox_url.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
    
    output_dir = "prints"
    os.makedirs(output_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    remote_ppm = f"/tmp/screenshot_{vmid}_{timestamp}.ppm"
    local_ppm = os.path.join(output_dir, f"{vmid}_{timestamp}.ppm")
    local_jpg = os.path.join(output_dir, f"{vmid}_{timestamp}.jpg")

    logger.info(f"Capturando screenshot da VM {vmid}...")

    try:
        # Re-conecta para garantir que a sessão esteja ativa na thread
        proxmox = get_proxmox_client(config)
        
        # --- Passo 1: envia screendump ao monitor QEMU ---
        # Note: O arquivo será salvo no disco do HOST PROXMOX
        proxmox.nodes(node).qemu(vmid).monitor.post(command=f"screendump {remote_ppm}")
        logger.debug(f"Screendump gerado no host: {remote_ppm}")
        time.sleep(1)

        # --- Passo 2: baixar o arquivo ---
        # Tentamos detectar se estamos rodando no próprio host Proxmox
        is_local = proxmox_host in ["localhost", "127.0.0.1"] or proxmox_host == os.uname().nodename
        
        if is_local and os.path.exists(remote_ppm):
            logger.debug("Rodando localmente no Proxmox. Movendo arquivo diretamente...")
            import shutil
            shutil.move(remote_ppm, local_ppm)
        else:
            logger.debug(f"Baixando arquivo de {proxmox_host} via SCP...")
            scp = subprocess.run(
                ["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                 f"root@{proxmox_host}:{remote_ppm}", local_ppm],
                capture_output=True, text=True
            )
            
            if scp.returncode != 0:
                logger.error(f"Falha no SCP: {scp.stderr.strip()}")
                context.update_result(vmid, {'ia_status': "❌ Falha SSH/SCP"})
                return

        # --- Passo 3: converte PPM -> JPG com Pillow ---
        if os.path.exists(local_ppm):
            img = Image.open(local_ppm).convert("RGB")
            img.save(local_jpg, "JPEG", quality=90, optimize=True)
            img.close()
            os.remove(local_ppm)
            logger.info(f"Screenshot salva com sucesso: {local_jpg}")
            
            # --- Análise via IA ---
            ia_label = "Falta análise"
            if config.analyze_with_gemini:
                try:
                    ia_data = analyze_image_with_gemini(local_jpg, config)
                    if ia_data:
                        if ia_data.get("boot_concluido") is True:
                            ia_label = "✅ OK"
                        else:
                            # Tenta extrair erro do dicionário retornado pela IA
                            ia_label = f"❌ Erro IA: {ia_data.get('erro', 'Boot não detectado')}"
                except Exception as e:
                    logger.error(f"Erro na análise Gemini: {e}")
                    ia_label = "❌ Falha Gemini"
            else:
                ia_label = "IA Desativada"
                
            context.update_result(vmid, {'ia_status': ia_label})
    except Exception as e:
        logger.error(f"Erro inesperado no screenshot para VM {vmid}: {e}")
        context.update_result(vmid, {'ia_status': "❌ Erro Processo"})

        
        # --- Passo 4: remove o PPM temporário do servidor Proxmox ---
        subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             f"root@{proxmox_host}", f"rm -f {remote_ppm}"],
            capture_output=True
        )
        logger.debug("Arquivo temporário removido do host Proxmox.")

    except Exception as e:
        logger.error(f"Exceção ao capturar screenshot da VM {vmid}: {e}")
