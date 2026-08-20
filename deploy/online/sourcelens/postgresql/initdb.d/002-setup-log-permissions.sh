#!/bin/bash
set -e

# Setup log directory permissions for PostgreSQL
# This script runs before PostgreSQL starts

LOG_DIR="/var/log/postgresql"

# Create log directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Set ownership to postgres user (UID 999 in PostgreSQL container)
chown -R postgres:postgres "$LOG_DIR"

# Set permissions
chmod -R 755 "$LOG_DIR"

echo "PostgreSQL log directory permissions set successfully"
