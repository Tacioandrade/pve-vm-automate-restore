import os
import logging
from pbs_restore.exceptions import VMFileError
from pbs_restore.constants import DEFAULT_VMS_FILE

logger = logging.getLogger(__name__)

def get_vms_to_restore(filename=DEFAULT_VMS_FILE, count=1):
    if not os.path.exists(filename):
        raise VMFileError(f"Arquivo não encontrado: {filename}")
        
    vms = []
    seen = set()
    
    with open(filename, 'r') as f:
        for i, line in enumerate(f, 1):
            content = line.strip()
            if not content:
                continue
            
            # Validation: 100-999999999
            try:
                vmid = int(content)
                if not (100 <= vmid <= 999999999):
                    raise ValueError()
                
                vmid_str = str(vmid)
                if vmid_str in seen:
                    logger.warning(f"VMID duplicado ignorado na linha {i}: {content}")
                    continue
                
                vms.append(vmid_str)
                seen.add(vmid_str)
                
            except ValueError:
                logger.warning(f"Linha {i} do arquivo '{filename}' inválida (não é um VMID válido): '{content}'")
                continue
    
    if not vms:
        raise VMFileError(f"Nenhum VMID válido encontrado no arquivo {filename}")
        
    return vms[:count]

def rotate_vm(filename, vmid):
    if not os.path.exists(filename):
        return
    try:
        with open(filename, 'r') as f:
            lines = [line.strip() for line in f if line.strip()]
            
        vmid_str = str(vmid)
        if vmid_str in lines:
            lines.remove(vmid_str)
            lines.append(vmid_str)
            with open(filename, 'w') as f:
                for line in lines:
                    f.write(line + '\n')
    except Exception as e:
        logger.error(f"Erro ao rotacionar VMID {vmid} no arquivo {filename}: {e}")
