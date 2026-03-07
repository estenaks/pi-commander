sudo apt-get update
sudo apt-get install -y nginx
sudo systemctl enable nginx
sudo rm -f /etc/nginx/sites-enabled/default
sudo rm -f /etc/nginx/sites-available/default
sudo touch /etc/nginx/sites-available/pi-commander
sudo cp /home/pi/pi-commander/utils/nginx-site /etc/nginx/sites-available/pi-commander
sudo ln -sf /etc/nginx/sites-available/pi-commander /etc/nginx/sites-enabled/pi-commander
sudo nginx -t
sudo systemctl reload nginx