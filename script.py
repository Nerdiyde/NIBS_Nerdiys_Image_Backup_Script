#                                  _   _                 _  _               _
#                                 | \ | |               | |(_)             | |
#  __      ____      ____      __ |  \| |  ___  _ __  __| | _  _   _     __| |  ___
#  \ \ /\ / /\ \ /\ / /\ \ /\ / / | . ` | / _ \| '__|/ _` || || | | |   / _` | / _ \
#   \ V  V /  \ V  V /  \ V  V /_ | |\  ||  __/| |  | (_| || || |_| | _| (_| ||  __/
#    \_/\_/    \_/\_/    \_/\_/(_)|_| \_| \___||_|   \__,_||_| \__, |(_)\__,_| \___|
#                                                               __/ |
#                                                              |___/
#     Infos on https://www.Nerdiy.de/
#
#     This program is free software: you can redistribute it and/or modify
#     it under the terms of the GNU General Public License as published by
#     the Free Software Foundation, either version 3 of the License, or
#     (at your option) any later version.
#
#     This program is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU General Public License for more details.
#
#     You should have received a copy of the GNU General Public License
#     along with this program.  If not, see <http://www.gnu.org/licenses/>.#
#     You can find additional infos about the licensing here: https://nerdiy.de/en/lizenz/
#

import os
import time
import paho.mqtt.client as mqtt
import subprocess
import json
import logging
import socket
import yaml
import signal
from datetime import datetime
import threading


# Logging to stdout (captured by journalctl as well)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[
    logging.StreamHandler()
])

# Prepare compression state file
COMPRESSION_STATE_FILE = os.path.join(os.path.dirname(__file__), "compression_state.json")

# Load configuration file
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.yaml")

def load_config():
    try:
        with open(CONFIG_FILE, "r") as file:
            return yaml.safe_load(file)
    except Exception as e:
        logging.error(f"Error at loading the configuration file: {e}")
        exit(1)

config = load_config()

# MQTT configuration
MQTT_BROKER = config["mqtt_broker"]
MQTT_PORT = config["mqtt_port"]
MQTT_USERNAME = config.get("mqtt_username", None)
MQTT_PASSWORD = config.get("mqtt_password", None)
MQTT_BASE_TOPIC = f"nerdiys_image_backup/{socket.gethostname()}"
MQTT_DISCOVERY_PREFIX = "homeassistant"
MQTT_DEVICE_NAME = f"nerdiys_backup_{socket.gethostname()}"
MQTT_RECONNECT_INTERVAL = config.get("mqtt_reconnect_interval", 10)  # in Sekunden, einstellbar über config.yaml

# SMB and backup settings
SMB_SHARE = config["smb_share"]
MOUNT_POINT = config["mount_point"]
SMB_USERNAME = config["smb_username"]
SMB_PASSWORD = config["smb_password"]
DISK_TO_BACKUP = config["disk_to_backup"]
RETAIN_BACKUPS = config["retain_backups"]
SMB_CHECK_INTERVAL = config.get("smb_check_interval", 60)

# Global backup process reference
backup_process = None

# Ensure the mount point directory exists
os.makedirs(MOUNT_POINT, exist_ok=True)

def mount_smb():
    logging.info("Attempting to mount SMB share...")
    result = os.system(f"sudo mount -t cifs {SMB_SHARE} {MOUNT_POINT} -o username={SMB_USERNAME},password={SMB_PASSWORD},iocharset=utf8,file_mode=0777,dir_mode=0777")
    if result == 0:
        logging.info("SMB share mounted successfully.")
        return True
    else:
        logging.error("Failed to mount SMB share!")
        return False

def unmount_smb():
    if os.path.ismount(MOUNT_POINT):
        logging.info("Unmounting SMB share...")
        os.system(f"sudo umount {MOUNT_POINT}")

def check_smb_permissions():
    logging.info("Checking SMB share and permissions...")

    # Mount SMB share if it's not already mounted
    if not os.path.ismount(MOUNT_POINT):
        logging.info("SMB drive is not mounted, attempting to mount...")
        if not mount_smb():
            logging.error("Failed to mount SMB share.")
            return False
    else:
        logging.info("SMB drive is already mounted.")

    test_file = os.path.join(MOUNT_POINT, "smb_test.tmp")

    try:
        # Test write access
        with open(test_file, "w") as f:
            f.write("Test")
        logging.info("Write access to SMB share successful.")
        
        # Test read access
        try:
            with open(test_file, "r") as f:
                content = f.read()
                if content != "Test":
                    raise PermissionError("Content of test file not read correctly from SMB share.")
        except IOError:
            raise PermissionError("Read access missing or file not readable on SMB share.")
        logging.info("Read access to SMB share successful.")
        
        # Test delete access
        try:
            os.remove(test_file)
        except PermissionError:
            raise PermissionError("Delete permissions missing, test file could not be removed.")
        logging.info("Delete access to SMB share successful.")

    except Exception as e:
        logging.error(f"SMB permission check failed: {e}")
        exit(1)

    except PermissionError as e:
        logging.error(f"SMB permission check failed: {e}")
        return False
    except IOError as e:
        logging.error(f"I/O error during SMB permission check: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error during SMB permission check: {e}")
        return False
    finally:
        unmount_smb()

# Signal handler to unmount SMB share on script termination
def signal_handler(sig, frame):
    logging.info("Script is terminating. Unmounting SMB share...")
    unmount_smb()
    exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# MQTT callback functions
def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        logging.info("Successfully connected to MQTT broker.")
        client.subscribe(f"{MQTT_BASE_TOPIC}/command")
        register_homeassistant()
        client.publish(f"{MQTT_BASE_TOPIC}/status", "Ready")
        client.loop(0.1)
    else:
        logging.error(f"Failed to connect to MQTT broker. Code: {reason_code}")

def on_message(client, userdata, message):
    global backup_process
    payload = message.payload.decode().strip().lower()
    logging.info(f"Message received: Topic: {message.topic}, Payload: '{payload}'")
    if message.topic == f"{MQTT_BASE_TOPIC}/command":
        if payload == "start":
            logging.info("Received command to start backup.")
            client.publish(f"{MQTT_BASE_TOPIC}/status", "Backup started")
            client.loop(0.1)
            threading.Thread(target=start_backup, args=(client,)).start()
        elif payload == "stop":
            logging.info("Received command to stop backup.")
            stop_backup(client)
        elif message.topic == f"{MQTT_BASE_TOPIC}/compression_enabled/set":
            if payload in ["true", "false"]:
                enable = payload == "true"
                save_compression_state(enable)
                client.publish(f"{MQTT_BASE_TOPIC}/compression_enabled/state", payload, retain=True)
                logging.info(f"Compression {'enabled' if enable else 'disabled'} via MQTT command.")
        else:
            logging.warning(f"Unknown command received: {payload}")

def on_disconnect(client, userdata, reason_code, properties, *args):
    if reason_code != 0:
        logging.warning(f"MQTT connection lost (rc={reason_code}). Trying to reconnect in {MQTT_RECONNECT_INTERVAL} seconds...")

def register_homeassistant():
    logging.info("Registering Home Assistant auto-discovery...")
    device_info = {
        "identifiers": [MQTT_DEVICE_NAME],
        "name": f"NIB on {socket.gethostname()}",
        "model": "Nerdiy's Image Backup System",
        "manufacturer": "Nerdiy.de"
    }

    sensors = {
        "backup_ongoing": {
            "unique_id": f"nib_{socket.gethostname()}_backup_ongoing",
            "name": "Backup in Progress",
            "state_topic": f"{MQTT_BASE_TOPIC}/backup_ongoing",
            "object_id": f"backup_ongoing_{socket.gethostname().lower()}"
        },
        "backup_last_start": {
            "unique_id": f"nib_{socket.gethostname()}_last_start",
            "name": "Last Backup Start",
            "state_topic": f"{MQTT_BASE_TOPIC}/last_start",
            "object_id": f"last_start_{socket.gethostname().lower()}"
        },
        "backup_last_end": {
            "unique_id": f"nib_{socket.gethostname()}_last_end",
            "name": "Last Backup End",
            "state_topic": f"{MQTT_BASE_TOPIC}/last_end",
            "object_id": f"last_end_{socket.gethostname().lower()}"
        },
        "backup_last_status": {
            "unique_id": f"nib_{socket.gethostname()}_last_status",
            "name": "Was the last backup successful?",
            "state_topic": f"{MQTT_BASE_TOPIC}/last_status",
            "object_id": f"last_status_{socket.gethostname().lower()}"
        },
        "backup_count": {
            "unique_id": f"nib_{socket.gethostname()}_backup_count",
            "name": "Backup Count",
            "state_topic": f"{MQTT_BASE_TOPIC}/backup_count",
            "object_id": f"backup_count_{socket.gethostname().lower()}"
        },
        "backup_last_successful_file": {
            "unique_id": f"nib_{socket.gethostname()}_last_successful_file",
            "name": "Last Successful Backup File",
            "state_topic": f"{MQTT_BASE_TOPIC}/last_successful_file",
            "object_id": f"last_successful_file_{socket.gethostname().lower()}"
        },  
        "progress": {
            "unique_id": f"nib_{socket.gethostname()}_progress",
            "name": "Backup Progress",
            "state_topic": f"{MQTT_BASE_TOPIC}/progress",
            "unit_of_measurement": "%",
            "object_id": f"progress_{socket.gethostname().lower()}"
        },  
        "progress_detailed": {
            "unique_id": f"nib_{socket.gethostname()}_progress_detailed",
            "name": "Backup Progress (Detailed)",
            "state_topic": f"{MQTT_BASE_TOPIC}/progress_detailed",
            "object_id": f"progress_detailed_{socket.gethostname().lower()}",
            "enabled_by_default": False
        },
        "estimated_time_remaining": {
            "unique_id": f"nib_{socket.gethostname()}_estimated_time_remaining",
            "name": "Estimated Time Remaining (s)",
            "state_topic": f"{MQTT_BASE_TOPIC}/estimated_time_remaining",
            "unit_of_measurement": "s",
            "object_id": f"estimated_time_remaining_{socket.gethostname().lower()}"
        },
        "elapsed_time": {
            "unique_id": f"nib_{socket.gethostname()}_elapsed_time",
            "name": "Elapsed Time",
            "state_topic": f"{MQTT_BASE_TOPIC}/elapsed_time",
            "unit_of_measurement": "s",
            "object_id": f"elapsed_time_{socket.gethostname().lower()}"
        },
        "data_transferred": {
            "unique_id": f"nib_{socket.gethostname()}_data_transferred",
            "name": "Data Transferred",
            "state_topic": f"{MQTT_BASE_TOPIC}/data_transferred",
            "object_id": f"data_transferred_{socket.gethostname().lower()}"
        },
        "last_write_speed": {
            "unique_id": f"nib_{socket.gethostname()}_last_write_speed",
            "name": "Last Write Speed",
            "state_topic": f"{MQTT_BASE_TOPIC}/last_write_speed",
            "unit_of_measurement": "MB/s"
        },
        "smb_status": {
            "unique_id": f"nib_{socket.gethostname()}_smb_status",
            "name": "SMB Drive Status",
            "state_topic": f"{MQTT_BASE_TOPIC}/smb_status",
            "object_id": f"smb_status_{socket.gethostname().lower()}",
            "icon": "mdi:network"
        },

    }

    # Compression toggle switch
    compression_switch = {
        "unique_id": f"nib_{socket.gethostname()}_compression_enabled",
        "object_id": f"nib_{socket.gethostname()}_compression_enabled",
        "name": "Backup Compression Enabled",
        "state_topic": f"{MQTT_BASE_TOPIC}/compression_enabled/state",
        "command_topic": f"{MQTT_BASE_TOPIC}/compression_enabled/set",
        "payload_on": "true",
        "payload_off": "false",
        "device": device_info
    }
    client.publish(f"{MQTT_DISCOVERY_PREFIX}/switch/{MQTT_DEVICE_NAME}/compression_enabled/config", json.dumps(compression_switch), retain=True)


    for sensor, cfg in sensors.items():

        sensor_config = {
            "name": f"{cfg['name']} ({socket.gethostname()})",
            "state_topic": cfg["state_topic"],
            "unique_id": cfg["unique_id"],
            "object_id": cfg["unique_id"], 
            "device": device_info
        }

        if "device_class" in cfg:
            sensor_config["device_class"] = cfg["device_class"]

        if "unit_of_measurement" in cfg:
            sensor_config["unit_of_measurement"] = cfg["unit_of_measurement"]

        if "enabled_by_default" in cfg:
            sensor_config["enabled_by_default"] = cfg["enabled_by_default"]

        client.publish(f"{MQTT_DISCOVERY_PREFIX}/sensor/{MQTT_DEVICE_NAME}/{sensor}/config", json.dumps(sensor_config), retain=True)

    # Backup Start Button
    start_button = {
        "unique_id": f"nib_{socket.gethostname()}_backup_start",
        "object_id": f"nib_{socket.gethostname()}_backup_start",
        "name": "Start Backup",
        "command_topic": f"{MQTT_BASE_TOPIC}/command",
        "payload_press": "start",
        "device": device_info
    }
    client.publish(f"{MQTT_DISCOVERY_PREFIX}/button/{MQTT_DEVICE_NAME}/backup_start/config", json.dumps(start_button), retain=True)

    # Backup Stop Button
    stop_button = {
        "unique_id": f"nib_{socket.gethostname()}_backup_stop",
        "object_id": f"nib_{socket.gethostname()}_backup_stop",
        "name": "Stop Backup",
        "command_topic": f"{MQTT_BASE_TOPIC}/command",
        "payload_press": "stop",
        "device": device_info
    }
    client.publish(f"{MQTT_DISCOVERY_PREFIX}/button/{MQTT_DEVICE_NAME}/backup_stop/config", json.dumps(stop_button), retain=True)

def get_disk_size():
    try:
        output = subprocess.check_output(f"sudo blockdev --getsize64 {DISK_TO_BACKUP}", shell=True).decode().strip()
        return int(output)
    except Exception as e:
        logging.error(f"Error retrieving drive size: {e}")
        return None
    
def format_size(bytes_val):
    try:
        # Conversion: 1 GB = 1e9 Bytes
        return f"{round(bytes_val / 1e9, 1)} GB"
    except Exception as e:
        logging.error(f"Error formatting size: {e}")
        return f"{bytes_val} B"

def start_backup(client):
    global backup_process
    # If SMB is not mounted, we mount it
    compression_enabled = load_compression_state()
    if not os.path.ismount(MOUNT_POINT):
        if not mount_smb():
            client.publish(f"{MQTT_BASE_TOPIC}/status", "Mount failed")
            client.loop(0.1)
            return

    update_backup_count(client)
    client.publish(f"{MQTT_BASE_TOPIC}/backup_ongoing", "True")
    client.loop(0.1)
    last_start_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    client.publish(f"{MQTT_BASE_TOPIC}/last_start", last_start_str)
    client.loop(0.1)

    backup_name = f"nerdiys_image_backup_{socket.gethostname()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.img"
    image_path = f"{MOUNT_POINT}/{backup_name}"
    logging.info(f"Backup started: {image_path}")

    if compression_enabled:
        cmd = f"sudo dd if={DISK_TO_BACKUP} bs=1M status=progress | gzip > {image_path}.gz"
        image_path += ".gz"
        backup_name += ".gz"
        backup_process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, preexec_fn=os.setsid)
    else:
        cmd = ["sudo", "dd", f"if={DISK_TO_BACKUP}", f"of={image_path}", "bs=1M", "status=progress"]
        backup_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, preexec_fn=os.setsid)

    disk_size = get_disk_size()
    backup_start_time = time.time()

    if not disk_size:
        logging.error("Could not determine drive size. Progress display in percentage not available.")

    # Log dd output and send to MQTT 
    for line in backup_process.stdout:
        line = line.strip()

        # Check if the SMB share is still mounted
        if not os.path.ismount(MOUNT_POINT):
            logging.error("SMB share no longer available! Stopping backup.")
            stop_backup(client)
            unmount_smb()
            break

        if line:
            logging.info(line)
            client.publish(f"{MQTT_BASE_TOPIC}/progress_detailed", line)

            # Extract the number of bytes already copied (first number in the line)
            copied_bytes = None
            parts = line.split(" ")
            for part in parts:
                if part.isdigit():
                    copied_bytes = int(part)
                    break

            # Extract the write speed from the dd output
            speed_value = None
            parts_comma = line.split(",")
            if len(parts_comma) >= 3:
                speed_part = parts_comma[-1].strip()
                if "MB/s" in speed_part:
                    try:
                        # Conversion: MB/s to Bytes/s (assuming 1 MB = 1e6 Bytes)
                        speed_value = float(speed_part.replace("MB/s", "").strip()) * 1e6
                    except ValueError:
                        speed_value = None

            if copied_bytes and disk_size:
                progress_percent = round((copied_bytes / disk_size) * 100, 2)
                client.publish(f"{MQTT_BASE_TOPIC}/progress", progress_percent)

                # Publish transferred data as "x GB of y GB"
                data_transferred_str = f"{format_size(copied_bytes)} of {format_size(disk_size)}"
                client.publish(f"{MQTT_BASE_TOPIC}/data_transferred", data_transferred_str)

                if speed_value and speed_value > 0:
                    remaining_bytes = disk_size - copied_bytes
                    estimated_time = round(remaining_bytes / speed_value)
                    client.publish(f"{MQTT_BASE_TOPIC}/estimated_time_remaining", estimated_time)

            # Calculate and publish elapsed time (in seconds)
            elapsed_time = int(time.time() - backup_start_time)
            client.publish(f"{MQTT_BASE_TOPIC}/elapsed_time", elapsed_time)

            # NEW: Extract the write speed from the dd output
            speed_value = None
            parts_comma = line.split(",")
            if len(parts_comma) >= 3:
                speed_part = parts_comma[-1].strip()
                if "MB/s" in speed_part:
                    try:
                        speed_value = float(speed_part.replace("MB/s", "").strip())
                    except ValueError:
                        speed_value = None
            if speed_value is not None:
                client.publish(f"{MQTT_BASE_TOPIC}/last_write_speed", speed_value)

            client.loop(0.1)

    backup_process.wait()

    last_end_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if backup_process.returncode == 0:
        
        if config.get("verify_backup", False):
            # Verification of the created image (partial verification)
            image_path = f"{MOUNT_POINT}/{backup_name}"
            if verify_backup(image_path):
                logging.info("Backup successfully completed and verified.")
                client.publish(f"{MQTT_BASE_TOPIC}/status", "Backup successfully completed and verified.")
            else:
                logging.info("Backup was not successfully completed. Verification failed.")
                client.publish(f"{MQTT_BASE_TOPIC}/status", "Backup was not successfully completed. Verification failed.")
        else:
            logging.info("Backup successfully completed. (Not verified)")
            client.publish(f"{MQTT_BASE_TOPIC}/status", "Backup successfully completed. (Not verified)")

        client.publish(f"{MQTT_BASE_TOPIC}/last_status", "Success")
        client.publish(f"{MQTT_BASE_TOPIC}/last_successful_file", backup_name, retain=True)

        # Save the successful backup status
        save_backup_state(last_start_str, last_end_str, "Success", backup_name)


    else:
        logging.error("Backup failed.")
        client.publish(f"{MQTT_BASE_TOPIC}/status", "Backup failed")
        client.publish(f"{MQTT_BASE_TOPIC}/last_status", "Failed")
        save_backup_state(last_start_str, last_end_str, "Failed", "n/a")

    client.publish(f"{MQTT_BASE_TOPIC}/backup_ongoing", "False")
    client.publish(f"{MQTT_BASE_TOPIC}/last_end", last_end_str)


    if backup_process.returncode == 0:
        logging.info("Backup successfully completed.")
        client.publish(f"{MQTT_BASE_TOPIC}/last_status", "Success")
        client.publish(f"{MQTT_BASE_TOPIC}/last_successful_file", backup_name, retain=True)
        client.loop(0.1)
    else:
        logging.error("Backup failed.")
        client.publish(f"{MQTT_BASE_TOPIC}/status", "Backup failed")
        client.publish(f"{MQTT_BASE_TOPIC}/last_status", "Failed")
        client.loop(0.1)

    client.publish(f"{MQTT_BASE_TOPIC}/backup_ongoing", "False")
    client.publish(f"{MQTT_BASE_TOPIC}/last_end", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    client.loop(0.1)

    # If the backup was successful, perform cleanup of old backups
    if backup_process.returncode == 0:
        cleanup_backups(client)

    update_backup_count(client)

def stop_backup(client):
    global backup_process
    if backup_process and backup_process.poll() is None:
        logging.info("Stopping backup process...")
        try:
            logging.info("Sending SIGTERM to the process...")
            os.killpg(os.getpgid(backup_process.pid), signal.SIGTERM)
            try:
                return_code = backup_process.wait(timeout=30)
                logging.info(f"Backup process ended with return value: {return_code}")
            except subprocess.TimeoutExpired:
                logging.warning("Backup process is not responding, sending SIGKILL...")
                os.killpg(os.getpgid(backup_process.pid), signal.SIGKILL)
                backup_process.wait()  # Wait until the process is terminated
                logging.info("Backup process was forcibly terminated.")
        except Exception as e:
            logging.error(f"Error stopping the backup process: {e}")
    else:
        logging.info("No running backup process found.")

    unmount_smb()
    client.publish(f"{MQTT_BASE_TOPIC}/backup_ongoing", "False")
    client.loop(0.1)

def update_backup_count(client):
    try:
        backups = [f for f in os.listdir(MOUNT_POINT) if (f.endswith(".img") or f.endswith(".img.gz")) and socket.gethostname() in f]
        client.publish(f"{MQTT_BASE_TOPIC}/backup_count", len(backups))
        client.loop(0.1)
    except Exception as e:
        logging.error(f"Error updating the backup count: {e}")

def publish_initial_state(client):
    state_file = os.path.join(os.path.dirname(__file__), "backup_state.json")
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
    except Exception as e:
        logging.warning("No initial backup state found, using default values")
        state = {
            "last_start": "n/a",
            "last_end": "n/a",
            "last_status": "n/a",
            "last_successful_file": "n/a"
        }
    # Send initially stored values to Home Assistant
    client.publish(f"{MQTT_BASE_TOPIC}/last_start", state.get("last_start", "n/a"))
    client.publish(f"{MQTT_BASE_TOPIC}/last_end", state.get("last_end", "n/a"))
    client.publish(f"{MQTT_BASE_TOPIC}/last_status", state.get("last_status", "n/a"))
    client.publish(f"{MQTT_BASE_TOPIC}/last_successful_file", state.get("last_successful_file", "n/a"), retain=True)
    
    # Initialize other sensors with meaningful starting values:
    client.publish(f"{MQTT_BASE_TOPIC}/backup_ongoing", "False")
    client.publish(f"{MQTT_BASE_TOPIC}/backup_count", 0)
    client.publish(f"{MQTT_BASE_TOPIC}/progress", 0)
    client.publish(f"{MQTT_BASE_TOPIC}/estimated_time_remaining", 0)
    client.publish(f"{MQTT_BASE_TOPIC}/elapsed_time", 0)
    client.publish(f"{MQTT_BASE_TOPIC}/last_write_speed", 0)

    disk_sz = get_disk_size() or 0
    client.publish(f"{MQTT_BASE_TOPIC}/data_transferred", f"0 of {format_size(disk_sz)}")

    compression_state = load_compression_state()
    client.publish(f"{MQTT_BASE_TOPIC}/compression_enabled/state", "true" if compression_state else "false", retain=True)

    client.loop(0.1)

def save_backup_state(last_start, last_end, last_status, last_successful_file):
    state = {
        "last_start": last_start,
        "last_end": last_end,
        "last_status": last_status,
        "last_successful_file": last_successful_file
    }
    state_file = os.path.join(os.path.dirname(__file__), "backup_state.json")
    try:
        with open(state_file, "w") as f:
            json.dump(state, f)
        logging.info("Backup status successfully saved.")
    except Exception as e:
        logging.error(f"Error saving the backup status: {e}")

def load_compression_state():
    try:
        with open(COMPRESSION_STATE_FILE, "r") as f:
            state = json.load(f)
            return state.get("compression_enabled", False)
    except Exception:
        return False  # Default: compression disabled

def save_compression_state(enabled: bool):
    try:
        with open(COMPRESSION_STATE_FILE, "w") as f:
            json.dump({"compression_enabled": enabled}, f)
        logging.info(f"Compression state saved: {enabled}")
    except Exception as e:
        logging.error(f"Failed to save compression state: {e}")

def cleanup_backups(client):
    expected_size = get_disk_size()
    if not expected_size:
        logging.error("Could not determine expected drive size. Cleanup will be skipped.")
        return

    # List of all .img files in the mount directory
    backups = [f for f in os.listdir(MOUNT_POINT) if (f.endswith(".img") or f.endswith(".img.gz")) and socket.gethostname() in f]
    
    valid_backups = []
    for backup in backups:
        backup_path = os.path.join(MOUNT_POINT, backup)
        try:
            size = os.path.getsize(backup_path)
        except Exception as e:
            logging.error(f"Error retrieving size of {backup}: {e}")
            continue

        # Tolerance: e.g., 90% to 110% of the expected size
        if 0.9 * expected_size <= size <= 1.1 * expected_size:
            valid_backups.append((backup, os.path.getmtime(backup_path)))
        else:
            logging.warning(f"Backup {backup} has unexpected size: {format_size(size)} (expected size: {format_size(expected_size)})")

    # Sort by creation/modification time (oldest first)
    valid_backups.sort(key=lambda x: x[1])

    # As long as there are more valid backups than allowed, delete the oldest
    while len(valid_backups) > RETAIN_BACKUPS:
        oldest_backup, _ = valid_backups.pop(0)
        oldest_backup_path = os.path.join(MOUNT_POINT, oldest_backup)
        try:
            os.remove(oldest_backup_path)
            logging.info(f"Old backup {oldest_backup} has been deleted.")
        except Exception as e:
            logging.error(f"Error deleting backup {oldest_backup}: {e}")

    # Then update the backup count
    update_backup_count(client)
    
def check_smb_status():
    """
    Checks if the SMB drive is reachable.
    If the drive is not mounted, it attempts to mount it.
    After the check, it will be unmounted again if it was mounted here.
    Returns "online" if the drive is accessible, otherwise "offline" or "error".
    """
    mounted_here = False
    # If the drive is not yet mounted, we mount it
    if not os.path.ismount(MOUNT_POINT):
        logging.info("SMB drive is not mounted, trying to mount...")
        if not mount_smb():
            logging.error("SMB drive could not be mounted.")
            return "offline"
        mounted_here = True

    try:
        # Try to list the contents of the directory
        os.listdir(MOUNT_POINT)
        status = "online"
    except Exception as e:
        logging.error(f"Error accessing SMB drive: {e}")
        status = "error"
    finally:
        # If we mounted the drive in this function, we unmount it again
        if mounted_here:
            unmount_smb()
    return status

def smb_status_monitor(client):
    while True:
        status = check_smb_status()
        client.publish(f"{MQTT_BASE_TOPIC}/smb_status", status, retain=True)
        client.loop(0.1)
        time.sleep(SMB_CHECK_INTERVAL)

def verify_backup(image_path):
    """
    Performs a partial verification of the created image by
    comparing several segments (e.g., 1MB) at defined positions.
    This function is only executed if it is enabled in the configuration.
    """
    # Check if verification is enabled
    if not config.get("verify_backup", False):
        logging.info("Backup verification is disabled in the configuration.")
        return True

    # Read parameters from the configuration
    segment_count = config.get("verify_backup_segments", 4)
    segment_size = config.get("verify_backup_segment_size", 1 * 1024 * 1024)  # Default: 1 MB

    # Get the size of the source drive and the created image
    disk_size = get_disk_size()
    try:
        image_size = os.path.getsize(image_path)
    except Exception as e:
        logging.error(f"Error retrieving image size: {e}")
        return False

    # Basic size comparison
    if disk_size != image_size:
        logging.error("Size of the source drive and the image do not match.")
        return False

    block_size = 1024 * 1024  # We assume 1 MB blocks.
    # If segment_size is not exactly 1MB, we warn and set it to 1MB
    if segment_size != block_size:
        logging.warning("Segment size is set to 1 MB.")
        segment_size = block_size

    # Calculate evenly distributed offsets (in bytes) for the segments
    offsets = []
    if segment_count > 1:
        step = (disk_size - segment_size) / (segment_count - 1)
        for i in range(segment_count):
            offsets.append(int(i * step))
    else:
        offsets = [0]

    # Check the hash for each segment
    for offset in offsets:
        skip = offset // block_size  # dd works in blocks
        count = segment_size // block_size  # Number of blocks to read

        try:
            source_cmd = f"sudo dd if={DISK_TO_BACKUP} bs=1M skip={skip} count={count} status=none | sha256sum"
            source_hash = subprocess.check_output(source_cmd, shell=True, text=True).split()[0]

            image_cmd = f"dd if={image_path} bs=1M skip={skip} count={count} status=none | sha256sum"
            image_hash = subprocess.check_output(image_cmd, shell=True, text=True).split()[0]

            logging.info(f"Segment at offset {offset}: Source Hash: {source_hash}, Image Hash: {image_hash}")

            if source_hash != image_hash:
                logging.error(f"Verification of the segment at offset {offset} failed.")
                return False
        except Exception as e:
            logging.error(f"Error verifying the segment at offset {offset}: {e}")
            return False

    logging.info("Backup verification successful: All segments match.")
    return True


# Check SMB permissions
check_smb_permissions()

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USERNAME and MQTT_PASSWORD:
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect
client.reconnect_delay_set(min_delay=1, max_delay=MQTT_RECONNECT_INTERVAL)

# Repeated connection attempts at startup if the MQTT server is not reachable
while True:
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        break  # Connection successful, exit the loop
    except Exception as e:
        logging.error(f"MQTT connection failed: {e}. Retrying in {MQTT_RECONNECT_INTERVAL} seconds...")
        time.sleep(MQTT_RECONNECT_INTERVAL)

logging.info("Starting MQTT loop in the background...")
client.loop_start()
publish_initial_state(client)

threading.Thread(target=smb_status_monitor, args=(client,), daemon=True).start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    logging.info("Program is shutting down.")
