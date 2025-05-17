# Nerdiy's Image Backup Script

This Python script automates creating disk image backups of a specified drive on Linux systems, with seamless integration into Home Assistant via MQTT for easy monitoring and control. I use it to periodically backup the SD cards of the RaspberryPIs in my home network.

---

## Features

- **Full disk image backup** using `dd` with progress reporting.
- **Optional compression** of backups with gzip, toggleable via a Home Assistant switch.
- **Backup retention**: Automatically deletes old backups beyond a configured limit.
- **SMB/CIFS share support**: Mounts a network share to save backups remotely.
- **Backup verification** (optional): Partial checksum verification of created images.
- **MQTT integration** with Home Assistant auto-discovery for:
  - Start and stop backup commands via MQTT topics or buttons in Home Assistant.
  - Sensors reporting backup status, progress, last backup time, success/failure, transfer speed, and SMB status.
  - Compression enable/disable switch.
- **Automatic reconnection** to MQTT broker and SMB share monitoring.
- **Graceful shutdown**: Ensures SMB shares are unmounted on script termination.
- **Detailed logging** for debugging and monitoring.

---

## Requirements

- Linux system with Python 3 installed.
- `dd`, `gzip`, and `sha256sum` available on the system.
- Network SMB/CIFS share accessible with proper credentials.
- MQTT broker accessible (e.g., Mosquitto).
- Home Assistant (optional) for MQTT integration and control.
- Python packages: `paho-mqtt`, `PyYAML`.

---

## Installation

### 1. Clone or download this repository.

### 2. Install system dependencies (Debian/Ubuntu example):

```bash
sudo apt update
sudo apt install -y python3 python3-pip cifs-utils
```

- `python3`, `python3-pip`: Python runtime and package manager  
- `cifs-utils`: needed for mounting SMB shares  
- `dd`, `gzip`, and `sha256sum` are usually preinstalled on Linux

### 3. Install Python dependencies:

```bash
pip3 install --upgrade pip
pip3 install paho-mqtt PyYAML
```

### 4. Configure the script by editing the `config.yaml` file with your settings:
- MQTT broker address and credentials  
- SMB share path, credentials, and mount point  
- Disk device to backup (e.g., `/dev/sda`)  
- Number of backups to retain  
- Enable backup verification (optional)  
- MQTT reconnect interval and other options

### 5. Run the script manually with root privileges (required for `dd` and mounting):

```bash
sudo python3 script.py
```

---

## Running the script as a systemd service

Create a systemd service file `/etc/systemd/system/nerdiy_backup.service` with the following content:

```ini
[Unit]
Description=Nerdiy Disk Image Backup Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/path/to/your/script/directory
ExecStart=/usr/bin/python3 /path/to/your/script/directory/script.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Replace `/path/to/your/script/directory` with the actual absolute path where `script.py` is located.**

### Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable nerdiy_backup.service
sudo systemctl start nerdiy_backup.service
```

### Check service status and logs:

```bash
sudo systemctl status nerdiy_backup.service
sudo journalctl -u nerdiy_backup.service -f
```

---

## Usage with Home Assistant

- The script automatically publishes MQTT discovery messages to Home Assistant.
- You will find the following entities created:
  - **Buttons** to start and stop backups.
  - **Sensors** for backup status, progress, last start/end times, success/failure, transfer speed, SMB status, backup count, and last successful backup file.
  - **Switch** to enable or disable compression for backups.

Control the backup and monitor its status directly from the Home Assistant UI.

---

## Backup Compression

- Backup compression can be toggled **on or off** via the Home Assistant switch `Backup Compression Enabled`.
- When enabled, the disk image is compressed on-the-fly with `gzip`.
- Compressed backups have the `.img.gz` extension and are managed alongside uncompressed backups.

---

## Notes

- The script requires root permissions to access raw disk devices and mount SMB shares.
- Make sure your SMB share has read/write/delete permissions for the backup operation.
- The backup verification feature reads and compares segments of the source disk and the created image via SHA256 checksums.
- The script automatically cleans up old backups beyond the configured retention count.

---

## License

GNU General Public License 3.0

---

## Author

Fabian @ Nerdiy.de (https://nerdiy.de) — 

---

## Support & Contributions

Feel free to open issues or pull requests on GitHub for improvements, bug reports, or feature requests.

---
