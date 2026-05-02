#!/usr/bin/env bash
# One-time setup for GitHub Actions auto-deploy.
#
# Run this on YOUR Mac. It:
#   1. Generates a dedicated SSH deploy key (no passphrase)
#   2. Copies the public key to the server (you'll be prompted once)
#   3. Prints the private key + host details for you to paste into GitHub
#
# After this completes, every `git push origin main` will auto-deploy
# WillCraft to https://will.alantanjb.com via .github/workflows/deploy.yml

set -euo pipefail

KEY_PATH="$HOME/.ssh/willcraft_deploy_ed25519"
SERVER_USER="ubuntu"
SERVER_HOST="52.221.231.214"

# 1. Generate key if not present
if [ ! -f "$KEY_PATH" ]; then
  echo "→ Generating SSH deploy key at $KEY_PATH"
  ssh-keygen -t ed25519 -N "" -C "willcraft-deploy" -f "$KEY_PATH"
else
  echo "→ Reusing existing key at $KEY_PATH"
fi

# 2. Append public key to server's authorized_keys
echo "→ Adding public key to ${SERVER_USER}@${SERVER_HOST}:~/.ssh/authorized_keys"
echo "  (you'll be prompted for the password / existing key auth once)"
ssh-copy-id -i "${KEY_PATH}.pub" "${SERVER_USER}@${SERVER_HOST}"

# 3. Print the values for GitHub secrets
echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "Now go to GitHub → repo → Settings → Secrets and variables → Actions"
echo "Click 'New repository secret' for each of the following 3 secrets:"
echo "════════════════════════════════════════════════════════════════════"
echo ""
echo "Name:  DEPLOY_HOST"
echo "Value: ${SERVER_HOST}"
echo ""
echo "Name:  DEPLOY_USER"
echo "Value: ${SERVER_USER}"
echo ""
echo "Name:  DEPLOY_SSH_KEY"
echo "Value: (paste the BLOCK below, including BEGIN/END lines)"
echo "─────────────────────── private key (copy ↓) ────────────────────"
cat "$KEY_PATH"
echo "─────────────────────── private key (copy ↑) ────────────────────"
echo ""
echo "Once all 3 secrets are saved, every git push to main auto-deploys."
echo "Trigger manually anytime from the GitHub Actions tab → 'Deploy to"
echo "production' → 'Run workflow'."
