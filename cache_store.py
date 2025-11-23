import json
import os

def load_cache(path):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_cache(path, data_set):
    with open(path, "w") as f:
        json.dump(list(data_set), f, indent=2)
