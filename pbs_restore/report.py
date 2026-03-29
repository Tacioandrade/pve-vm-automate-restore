import threading
from pbs_restore.models import RestorationResult

class RestorationContext:
    def __init__(self):
        self.lock = threading.Lock()
        self.results = {} # vmid -> RestorationResult

    def add_result(self, result: RestorationResult):
        with self.lock:
            self.results[result.vmid] = result

    def update_result(self, vmid, data_dict):
        with self.lock:
            if vmid not in self.results:
                self.results[vmid] = RestorationResult(vmid=vmid)
            
            # Update attributes from dictionary
            for key, value in data_dict.items():
                if hasattr(self.results[vmid], key):
                    setattr(self.results[vmid], key, value)

    def get_result(self, vmid) -> RestorationResult:
        with self.lock:
            return self.results.get(vmid)
