#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
sdk_dir="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}"
key_dir="${XDG_DATA_HOME:-$HOME/.local/share}/instabatch-android"
key_file="$key_dir/release-key.p12"
key_alias="instabatch"

command -v keytool >/dev/null || { printf 'keytool is required; install a JDK.\n' >&2; exit 1; }
command -v gradle >/dev/null || { printf 'Gradle is required.\n' >&2; exit 1; }
[[ -d "$sdk_dir" ]] || { printf 'Android SDK not found at %s.\n' "$sdk_dir" >&2; exit 1; }

mkdir -p "$key_dir"
chmod 700 "$key_dir"
read -r -s -p 'Release keystore password: ' signing_password </dev/tty
printf '\n'
[[ -n "$signing_password" ]] || { printf 'Password cannot be empty.\n' >&2; exit 1; }

export INSTABATCH_KEYSTORE_FILE="$key_file"
export INSTABATCH_KEYSTORE_PASSWORD="$signing_password"
export INSTABATCH_KEY_ALIAS="$key_alias"
export INSTABATCH_KEY_PASSWORD="$signing_password"
unset signing_password

if [[ ! -f "$key_file" ]]; then
  read -r -s -p 'Confirm password for the new signing key: ' confirmation </dev/tty
  printf '\n'
  [[ "$confirmation" == "$INSTABATCH_KEYSTORE_PASSWORD" ]] || { printf 'Passwords do not match.\n' >&2; exit 1; }
  unset confirmation
  keytool -genkeypair -v -keystore "$key_file" -storetype PKCS12 \
    -storepass:env INSTABATCH_KEYSTORE_PASSWORD -keypass:env INSTABATCH_KEY_PASSWORD \
    -alias "$key_alias" -keyalg RSA -keysize 2048 -validity 10000 \
    -dname 'CN=InstaBatch Release' -noprompt
  chmod 600 "$key_file"
fi

export ANDROID_HOME="$sdk_dir"
export ANDROID_SDK_ROOT="$sdk_dir"
gradle -p "$project_dir" assembleRelease --console=plain
printf '\nSigned APK: %s\n' "$project_dir/app/build/outputs/apk/release/app-release.apk"