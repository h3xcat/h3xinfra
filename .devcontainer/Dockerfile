ARG BASE_IMAGE_NAME=mcr.microsoft.com/devcontainers/base
ARG BASE_IMAGE_TAG=jammy

FROM ${BASE_IMAGE_NAME}:${BASE_IMAGE_TAG}

USER root

ARG USERNAME=vscode
ARG VSC_SERVER_PATH="/home/${USERNAME}/.vscode-server"

RUN apt-get update && apt-get install -y sudo iputils-ping dnsutils keychain pwgen python3-pip python3-venv \
    && apt-get clean -y \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 "${USERNAME}" && useradd --uid 1001 --gid "${USERNAME}" --shell /bin/bash --create-home "${USERNAME}"
RUN echo "eval \`keychain --eval --quiet\`" >> /home/${USERNAME}/.bashrc

RUN echo "${USERNAME} ALL=(root) NOPASSWD:ALL" >> "/etc/sudoers.d/${USERNAME}" \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
    && mkdir /commandhistory \
    && touch /commandhistory/.bash_history \
    && chown -R "${USERNAME}" /commandhistory \
    && echo "$SNIPPET" >> "/home/${USERNAME}/.bashrc"

# Install kubectl
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"

# Install helm
RUN curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash


USER ${USERNAME}
