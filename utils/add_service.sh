sudo cat /home/pi/pi-commander/utils/pi-commander-webserver.service > /etc/systemd/system/pi-commander-webserver.service
sudo systemctl daemon-reload
sudo systemctl enable pi-commander-webserver.service
sudo systemctl start pi-commander-webserver.service 