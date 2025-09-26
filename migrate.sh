#!/bin/sh

# Initialize migrations directory if it doesn't exist
if [ ! -d "migrations" ]; then
    echo "Initializing migrations directory..."
    flask db init
    if [ $? -ne 0 ]; then
        echo "Failed to initialize migrations directory."
        exit 1
    fi
    echo "Migrations directory initialized."
fi

# Generate migration script
echo "Generating migration script..."
flask db migrate -m "Add industry and market columns to Stock table"
if [ $? -ne 0 ]; then
    echo "Failed to generate migration script."
    exit 1
fi

# Apply migration
echo "Applying migration..."
flask db upgrade
if [ $? -ne 0 ]; then
    echo "Failed to apply migration."
    exit 1
fi

echo "Migration completed successfully."