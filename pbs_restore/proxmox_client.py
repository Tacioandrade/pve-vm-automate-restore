import time
import datetime
import os
import urllib3
import logging
from proxmoxer import ProxmoxAPI
from tenacity import retry, wait_exponential, stop_after_attempt
from pbs_restore.exceptions import (
    PBSRestoreError, ProxmoxConnectionError, BackupNotFoundError, RestoreError
)


# Disable insecure request warnings when verify_ssl=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


@retry(
    wait=wait_exponential(multiplier=1, min=4, max=10),
    stop=stop_after_attempt(3),
    reraise=True
)
def check_connection(proxmox):
    """Verifica conexão com Proxmox API com sistema de retry."""
    return proxmox.version.get()


def get_proxmox_client(config):
    # Determine host and port from URL
    url = config.proxmox_url
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
        user=config.proxmox_user,
        password=config.proxmox_password,
        verify_ssl=False,
        timeout=config.proxmox_timeout
    )


def get_latest_backup(proxmox, node, storage, vmid):
    try:
        # Fetch contents of the backup storage
        content = proxmox.nodes(node).storage(storage).content.get()
        
        # Filter items for backps matching this VMID
        backups = [
            item for item in content 
            if item.get('content') == 'backup' and (f"/{vmid}/" in item.get('volid', '') or f"-{vmid}-" in item.get('volid', ''))
        ]
        
        if not backups:
            raise BackupNotFoundError(f"Nenhum backup encontrado para a VM {vmid}")
            
        # Sort backups by creation time (ctime) descending (newest first)
        backups.sort(key=lambda x: x.get('ctime', 0), reverse=True)
        return backups[0]
        
    except PBSRestoreError: # Wait, I should import PBSRestoreError if I use it
        raise
    except Exception as e:
        logger.error(f"Erro ao procurar backup da VM {vmid}: {e}")
        return None


def wait_for_task(proxmox, node, upid, timeout_minutes=120):
    """Polls the Proxmox task status until it completes with timeout and resilience."""
    start_time = time.time()
    last_status_time = time.time()
    consecutive_errors = 0
    timeout_seconds = timeout_minutes * 60

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            raise RestoreError(f"Timeout atingido aguardando tarefa {upid} ({timeout_minutes}m)")

        try:
            task = proxmox.nodes(node).tasks(upid).status.get()
            consecutive_errors = 0 # Reset on success
            
            if task.get('status') == 'stopped':
                return task.get('exitstatus', 'Desconhecido')
            
            # Print status every 60 seconds
            if time.time() - last_status_time > 60:
                logger.info(f"Aguardando tarefa {upid}, {int(elapsed/60)}m decorridos...")
                last_status_time = time.time()


        except PBSRestoreError:
            raise
        except Exception as e:
            consecutive_errors += 1
            logger.warning(f"Erro ao checar status da tarefa {upid} ({consecutive_errors}/5): {e}")
            if consecutive_errors >= 5:

                raise RestoreError(f"Múltiplas falhas consecutivas ao checar status da tarefa {upid}")
            
        time.sleep(5)

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
        logger.error(f"Erro ao verificar VMs existentes: {e}")
        # Retorna True em caso de falha de conexão na checagem pra evitar qualquer desastre

        return True
        
    return False

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
