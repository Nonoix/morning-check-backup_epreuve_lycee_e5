FROM python:3-slim-buster

# Copy .flake8 file
COPY .flake8 /

# Zero interaction while installing or upgrading the system via apt
ENV DEBIAN_FRONTEND noninteractive

# Copy requirements.txt for pip
COPY process/requirements.txt /

# Install require debian packages
RUN apt-get -qq update && apt-get -qq install python3-setuptools python3-wheel -y

# Install pip requirements.txt packages
RUN pip install -U pip
RUN pip install flake8 --quiet

# Keep container up
CMD tail -f /dev/null
