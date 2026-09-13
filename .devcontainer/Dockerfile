ARG BASE_IMAGE_NAME=mcr.microsoft.com/devcontainers/base
# Versioned tag so Renovate's built-in dockerfile manager can track the base
# image (a bare "jammy" is skipped as invalid-value); the -jammy suffix keeps
# updates on Ubuntu 22.04.
ARG BASE_IMAGE_TAG=3.0.8-jammy

FROM ${BASE_IMAGE_NAME}:${BASE_IMAGE_TAG}

USER root

ARG USERNAME=vscode
ARG VSC_SERVER_PATH="/home/${USERNAME}/.vscode-server"

RUN apt-get update && apt-get install -y sudo iputils-ping dnsutils keychain pwgen python3-pip python3-venv bash-completion \
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

# kubectl/helm are pinned here on purpose: the kubectl-helm-minikube feature
# (removed from devcontainer.json) layered `latest` over anything installed in
# this stage, and kubectl must stay within +/-1 minor of the k3s cluster
# (renovate.json caps it via allowedVersions). Renovate bumps the ARGs through
# the generic `# renovate:` marker manager.
# renovate: datasource=github-releases depName=kubernetes/kubernetes extractVersion=^v(?<version>.+)$
ARG KUBECTL_VERSION=1.37.0
# renovate: datasource=github-releases depName=helm/helm
ARG HELM_VERSION=v4.3.0

RUN curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl" \
    && chmod 0755 /usr/local/bin/kubectl \
    && kubectl completion bash > /etc/bash_completion.d/kubectl

# get-helm-3 installs the LATEST release unless --version is given; the tag in
# the URL only selects the script revision.
RUN curl -fsSL "https://raw.githubusercontent.com/helm/helm/${HELM_VERSION}/scripts/get-helm-3" | bash -s -- --version "${HELM_VERSION}" \
    && helm completion bash > /etc/bash_completion.d/helm

USER ${USERNAME}
