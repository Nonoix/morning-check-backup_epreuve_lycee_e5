FROM python:3-slim-buster

# Zero interaction while installing or upgrading the system via apt
ENV DEBIAN_FRONTEND noninteractive

# Copy requirements.txt for pip
COPY process/requirements.txt /

# Install require debian packages
RUN apt-get -qq update && apt-get -qq install python3-setuptools python3-wheel curl gnupg2 software-properties-common lsb-release libcap2-bin dialog apt-utils unixodbc

# Install require debian source list and update packages list
RUN curl -fsSL https://apt.releases.hashicorp.com/gpg | apt-key add -
RUN apt-add-repository "deb [arch=amd64] https://apt.releases.hashicorp.com $(lsb_release -cs) main"
RUN apt-get -qq update

# Install pip requirements.txt packages
RUN pip install -U pip
RUN pip install -r /requirements.txt

# Hashicorp Vault
RUN apt-get -qq install vault
RUN setcap cap_ipc_lock= /usr/bin/vault

# Set timezone
RUN rm -rf /etc/localtime && ln -s /usr/share/zoneinfo/Europe/Paris /etc/localtime

# Keep container up
CMD tail -f /dev/null
