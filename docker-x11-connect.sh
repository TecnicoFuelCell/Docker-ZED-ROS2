#!/bin/bash

CONTAINER_NAME="tfc"
XAUTH=/tmp/.docker.xauth
REMOTE_XAUTH_PATH="/root/.Xauthority"

# extract SSH-forwarded cookie
COOKIE=$(xauth list $DISPLAY | awk '{print $3}')
if [ -z "$COOKIE" ]; then
  echo "❌ Could not extract X11 cookie. Is DISPLAY=$DISPLAY valid?"
  exit 1
fi

# create a new .Xauthority file with that cookie
touch $XAUTH
xauth -f $XAUTH remove $DISPLAY >/dev/null 2>&1  # clean existing entry
xauth -f $XAUTH add $DISPLAY . $COOKIE

# copy the new Xauthority file to the container
docker cp $XAUTH "$CONTAINER_NAME":/tmp/.Xauthority.new

# overwrite the in-use file inside the container (avoids "device busy")
docker exec "$CONTAINER_NAME" sh -c "cat /tmp/.Xauthority.new > $REMOTE_XAUTH_PATH"

# enter the container with GUI support
docker exec -it \
  -e DISPLAY=$DISPLAY \
  -e XAUTHORITY=$REMOTE_XAUTH_PATH \
  "$CONTAINER_NAME" bash
