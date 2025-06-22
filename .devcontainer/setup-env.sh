#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

mkdir -p ~/.ssh
cp -r /tmp/host-ssh/. ~/.ssh
chmod 700 ~/.ssh
find ~/.ssh -type f -exec chmod 600 {} +
chown -R $(whoami):$(whoami) ~/.ssh

# Change ownership in the current directory and subdirectories to the current user
echo "Changing ownership of files in the current directory and subdirectories to \"$(whoami)\"..."
find . -exec sudo chown $(whoami):$(whoami) {} \;

# Fix permissions
find "$WORKSPACE_FOLDER/bin" -type f -exec chmod +x {} \; -print
find "$WORKSPACE_FOLDER" ! -user $(whoami) -exec echo "Changing ownership of: {}" \; -exec sudo chown $(whoami): {} \;

if [ -d "/commandhistory" ]; then
    find "/commandhistory" ! -user $(whoami) -exec echo "Changing ownership of: {}" \; -exec sudo chown $(whoami): {} \;
else
    echo "Directory /commandhistory not found, skipping ownership change"
fi


# Install Ansible
sudo apt-get update
sudo apt-get install -y software-properties-common
sudo add-apt-repository --yes --update ppa:ansible/ansible
sudo apt-get install -y ansible
sudo apt-get autoremove -y
sudo apt-get clean

ansible-galaxy collection install git+https://github.com/k3s-io/k3s-ansible.git
ansible-galaxy collection install kubernetes.core

# Install python dependencies
pip3 install --upgrade pip
pip3 install kubernetes

# Install helm-diff
helm plugin install https://github.com/databus23/helm-diff
