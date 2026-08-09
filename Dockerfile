# =============================================================================
# Dockerfile - ros2_ws (ROS 2 Jazzy / Ubuntu 24.04 / Python 3.12)
# =============================================================================

FROM osrf/ros:jazzy-desktop

ENV DEBIAN_FRONTEND=noninteractive
ENV ROS_DISTRO=jazzy
SHELL ["/bin/bash", "-c"]

# -----------------------------------------------------------------------------
# 1. Dependências de sistema base
# -----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git wget curl unzip \
        libboost-all-dev libtbb-dev libopencv-dev \
        python3-pip python3-colcon-common-extensions python3-rosdep python3-vcstool \
        libeigen3-dev \
        ros-jazzy-nmea-msgs \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 2. GTSAM a partir do source, instalado em /usr/local
# -----------------------------------------------------------------------------
RUN git clone --branch 4.2 --depth 1 https://github.com/borglab/gtsam.git /tmp/gtsam \
    && cd /tmp/gtsam && mkdir build && cd build \
    && cmake .. \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DGTSAM_USE_SYSTEM_EIGEN=ON \
        -DGTSAM_BUILD_TESTS=OFF \
        -DGTSAM_BUILD_EXAMPLES_ALWAYS=OFF \
        -DGTSAM_WITH_TBB=ON \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && rm -rf /tmp/gtsam

# -----------------------------------------------------------------------------
# 3. Dependências Python (Python 3.12 nativo do Jazzy)
# -----------------------------------------------------------------------------
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed \
        casadi \
        bezier \
        transforms3d \
        rosbags \
        ultralytics \
        torch --extra-index-url https://download.pytorch.org/whl/cpu

# -----------------------------------------------------------------------------
# 4. Automar o source do ROS 2 e Workspace no .bashrc (para novas sessões)
# -----------------------------------------------------------------------------
RUN echo "source /opt/ros/jazzy/setup.bash" >> /root/.bashrc \
    && echo "if [ -f /opt/share/workspace/install/setup.bash ]; then source /opt/share/workspace/install/setup.bash; fi" >> /root/.bashrc

# -----------------------------------------------------------------------------
# 5. Entrypoint e diretório de trabalho
# -----------------------------------------------------------------------------
WORKDIR /opt/share/workspace

RUN printf '#!/bin/bash\nset -e\nsource /opt/ros/jazzy/setup.bash\nif [ -f /opt/share/workspace/install/setup.bash ]; then\n    source /opt/share/workspace/install/setup.bash\nfi\nexec "$@"\n' > /ros_entrypoint.sh \
    && chmod +x /ros_entrypoint.sh

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]