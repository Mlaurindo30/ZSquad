import os
import json
import threading
import tempfile
import pathlib
import shutil
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from integrations.zcode_adapter import configure_zcode

def test_broken_json():
    print("Testing Broken JSON...")
    temp_dir = tempfile.mkdtemp()
    try:
        config_path = os.path.join(temp_dir, ".zcode", "config.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            f.write("{ invalid json")
        
        try:
            configure_zcode(zcode_config_path=config_path, squad_runtime_path=temp_dir)
            print("FAILED: Did not raise ValueError for broken JSON")
            return False
        except ValueError as e:
            print(f"PASSED: Caught ValueError as expected: {e}")
            
        with open(config_path, "r") as f:
            content = f.read()
            if content == "{ invalid json":
                print("PASSED: File content not corrupted")
            else:
                print("FAILED: File content was modified despite error")
                return False
        return True
    finally:
        shutil.rmtree(temp_dir)

def test_missing_directory():
    print("\nTesting Missing Directory...")
    temp_dir = tempfile.mkdtemp()
    try:
        config_path = os.path.join(temp_dir, ".zcode", "config.json")
        configure_zcode(zcode_config_path=config_path, squad_runtime_path=temp_dir)
        
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
                if "agent-squad" in config.get("mcpServers", {}):
                    print("PASSED: Directory and file created successfully")
                    return True
        print("FAILED: Directory or file not created")
        return False
    finally:
        shutil.rmtree(temp_dir)

def test_data_integrity():
    print("\nTesting Data Integrity (Merging)...")
    temp_dir = tempfile.mkdtemp()
    try:
        config_path = os.path.join(temp_dir, ".zcode", "config.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        existing_data = {
            "mcpServers": {
                "other-server": {
                    "command": "echo"
                }
            },
            "other_setting": True
        }
        with open(config_path, "w") as f:
            json.dump(existing_data, f)
            
        configure_zcode(zcode_config_path=config_path, squad_runtime_path=temp_dir)
        
        with open(config_path, "r") as f:
            config = json.load(f)
            if "other-server" in config["mcpServers"] and "agent-squad" in config["mcpServers"] and config["other_setting"]:
                print("PASSED: Data integrity maintained after merge")
                return True
            else:
                print("FAILED: Existing data was overwritten or lost")
                return False
    finally:
        shutil.rmtree(temp_dir)

def test_concurrency():
    print("\nTesting Concurrency (Atomic Write)...")
    temp_dir = tempfile.mkdtemp()
    try:
        config_path = os.path.join(temp_dir, ".zcode", "config.json")
        
        def run_adapter():
            try:
                configure_zcode(zcode_config_path=config_path, squad_runtime_path=temp_dir)
            except Exception as e:
                print(f"Thread failed: {e}")
                
        threads = []
        for _ in range(50):
            t = threading.Thread(target=run_adapter)
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        with open(config_path, "r") as f:
            config = json.load(f)
            if "agent-squad" in config.get("mcpServers", {}):
                print("PASSED: Concurrent execution did not corrupt JSON")
                return True
            else:
                print("FAILED: JSON corrupted or missing data after concurrency")
                return False
    except Exception as e:
        print(f"FAILED: Concurrency test exception: {e}")
        return False
    finally:
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    results = [
        test_broken_json(),
        test_missing_directory(),
        test_data_integrity(),
        test_concurrency()
    ]
    if all(results):
        print("\nALL TESTS PASSED")
        sys.exit(0)
    else:
        print("\nSOME TESTS FAILED")
        sys.exit(1)
