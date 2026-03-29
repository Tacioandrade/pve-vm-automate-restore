import os
import time
import logging

logger = logging.getLogger(__name__)

def cleanup_old_artifacts(directory, days=7):
    """Remove arquivos mais velhos que 'days' dias no diretório especificado."""
    if not os.path.exists(directory):
        return
        
    now = time.time()
    cutoff = now - (days * 86400)
    
    count = 0
    try:
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    count += 1
        
        if count > 0:
            logger.info(f"Limpeza concluída em '{directory}': {count} arquivos removidos.")
    except Exception as e:
        logger.error(f"Erro durante limpeza de artefatos em '{directory}': {e}")
