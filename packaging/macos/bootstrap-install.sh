#!/bin/sh
set -u

UV_VERSION="0.11.28"
UV_INSTALL_URL="https://astral.sh/uv/${UV_VERSION}/install.sh"
SETUP_ROOT=${SOUNDCAPSULE_SETUP_ROOT:-"/Library/Application Support/SoundCapsule/Setup"}
INSTALLED_APP=${SOUNDCAPSULE_INSTALLED_APP:-"/Applications/Sound Capsule.app"}

if [ "$(id -u)" -eq 0 ] && [ "${1:-}" != "--as-user" ]; then
    console_user=$(stat -f '%Su' /dev/console 2>/dev/null || true)
    if [ -z "$console_user" ] || [ "$console_user" = "root" ] || [ "$console_user" = "loginwindow" ]; then
        # A user launched later will be provisioned by the app's repair flow.
        exit 0
    fi
    console_uid=$(id -u "$console_user")
    console_home=$(dscl . -read "/Users/$console_user" NFSHomeDirectory 2>/dev/null | sed 's/^[^ ]* //')
    exec launchctl asuser "$console_uid" sudo -H -u "$console_user" \
        env HOME="$console_home" SOUNDCAPSULE_SETUP_ROOT="$SETUP_ROOT" \
        SOUNDCAPSULE_INSTALLED_APP="$INSTALLED_APP" "$0" --as-user
fi

log_dir="$HOME/Library/Logs/SoundCapsule"
log_file="$log_dir/install.log"
failure_file="$HOME/Library/Application Support/SoundCapsule/setup-failed.txt"
mkdir -p "$log_dir" "$(dirname "$failure_file")"
exec >>"$log_file" 2>&1

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') provisioning Sound Capsule"

find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi
    for candidate in \
        "$HOME/.local/bin/uv" \
        "$HOME/.cargo/bin/uv" \
        /opt/homebrew/bin/uv \
        /usr/local/bin/uv; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

uv_path=$(find_uv || true)
if [ -z "$uv_path" ]; then
    echo "uv was not found; attempting the official uv ${UV_VERSION} installer"
    installer=$(mktemp -t sound-capsule-uv.XXXXXX)
    if ! curl --proto '=https' --tlsv1.2 -LsSf "$UV_INSTALL_URL" -o "$installer" \
        || ! sh "$installer"; then
        rm -f "$installer"
        printf '%s\n' "uv installation failed. Install uv from https://docs.astral.sh/uv/getting-started/installation/ and retry setup." >"$failure_file"
        exit 1
    fi
    rm -f "$installer"
    uv_path=$(find_uv || true)
fi

if [ -z "$uv_path" ]; then
    printf '%s\n' "uv could not be located after installation. Install it from https://docs.astral.sh/uv/getting-started/installation/ and retry setup." >"$failure_file"
    exit 1
fi

if ! "$uv_path" run --python 3.12 "$SETUP_ROOT/scripts/install.py" \
    --uv-executable "$uv_path" --installed-app "$INSTALLED_APP"; then
    printf '%s\n' "Sound Capsule provisioning failed. See $log_file and https://docs.astral.sh/uv/getting-started/installation/." >"$failure_file"
    exit 1
fi

legacy_app="$HOME/Applications/Sound Capsule.app"
if [ "$legacy_app" != "$INSTALLED_APP" ] && [ -d "$legacy_app" ]; then
    rm -rf "$legacy_app"
fi
legacy_vst="$HOME/Library/Audio/Plug-Ins/VST3/Sound Capsule.vst3"
system_vst="/Library/Audio/Plug-Ins/VST3/Sound Capsule.vst3"
if [ -d "$system_vst" ] && [ -d "$legacy_vst" ]; then
    rm -rf "$legacy_vst"
fi
rm -f "$failure_file"
echo "Sound Capsule provisioning complete"
