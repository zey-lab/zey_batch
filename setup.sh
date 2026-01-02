#!/bin/bash

# SMS Campaign Manager - Automated Setup Script
# This script performs complete environment setup

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_info() { echo -e "${BLUE}ℹ${NC} $1"; }
print_header() { echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; echo -e "${BLUE}  $1${NC}"; echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"; }

# Main setup
main() {
    print_header "SMS Campaign Manager - Automated Setup"

    # Step 1: Check UV installation
    print_info "Checking for UV package manager..."
    if ! command -v uv &> /dev/null; then
        print_error "UV is not installed"
        print_info "Installing UV..."
        curl -LsSf https://astral.sh/uv/install.sh | sh

        # Source the profile to get UV in PATH
        if [ -f "$HOME/.cargo/env" ]; then
            source "$HOME/.cargo/env"
        fi

        if command -v uv &> /dev/null; then
            print_success "UV installed successfully"
        else
            print_error "UV installation failed. Please install manually:"
            echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
            exit 1
        fi
    else
        print_success "UV is already installed ($(uv --version))"
    fi

    # Step 2: Install Python dependencies
    print_header "Installing Python Dependencies"
    print_info "Running uv sync..."
    if uv sync; then
        print_success "All dependencies installed"
    else
        print_error "Dependency installation failed"
        exit 1
    fi

    # Step 3: Create directory structure
    print_header "Creating Directory Structure"

    directories=(
        "data"
        "data/archive"
        "data/delete_temp"
        "config"
        "logs"
        "src/sms_campaign"
    )

    for dir in "${directories[@]}"; do
        if mkdir -p "$dir" 2>/dev/null; then
            print_success "Created/verified: $dir/"
        else
            print_warning "Could not create: $dir/"
        fi
    done

    # Step 4: Setup environment file
    print_header "Configuring Environment"

    if [ -f .env ]; then
        print_warning ".env file already exists - skipping creation"
        print_info "To reconfigure, delete .env and run setup again"
    else
        print_info "Creating .env from template..."
        if cp .env.example .env; then
            print_success ".env file created"

            # Make .env writable
            chmod 600 .env

            # Interactive configuration (optional)
            read -p "Would you like to configure Twilio credentials now? (y/N): " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                configure_twilio
            else
                print_warning "Remember to edit .env and add your Twilio credentials later"
            fi
        else
            print_error "Failed to create .env file"
            exit 1
        fi
    fi

    # Step 5: Setup sample data files
    print_header "Setting Up Sample Data Files"

    # Copy sample files if working files don't exist
    if [ ! -f data/customers_list_new.xlsx ]; then
        if [ -f data/customers_list_new.sample.xlsx ]; then
            print_info "Copying sample customer list..."
            cp data/customers_list_new.sample.xlsx data/customers_list_new.xlsx
            print_success "Sample customer list ready: data/customers_list_new.xlsx"
        else
            print_warning "Sample customer list not found - you'll need to create it"
        fi
    else
        print_success "Customer list already exists: data/customers_list_new.xlsx"
    fi

    if [ ! -f data/campaigns.xlsx ]; then
        if [ -f data/campaigns.sample.xlsx ]; then
            print_info "Copying sample campaign configuration..."
            cp data/campaigns.sample.xlsx data/campaigns.xlsx
            print_success "Sample campaigns ready: data/campaigns.xlsx"
        else
            print_warning "Sample campaigns not found - you'll need to create it"
        fi
    else
        print_success "Campaigns file already exists: data/campaigns.xlsx"
    fi

    # Step 6: Verify setup
    print_header "Verifying Installation"

    print_info "Running setup verification tests..."
    if uv run python test_setup.py; then
        print_success "All verification tests passed!"
    else
        print_warning "Some tests failed - check output above"
    fi

    # Step 7: Create helper aliases (optional)
    print_header "Creating Helper Scripts"
    create_helper_scripts

    # Final summary
    print_header "Setup Complete!"

    echo ""
    echo "📁 Project Structure:"
    echo "   ├── data/                    (Customer and campaign data)"
    echo "   ├── config/                  (Configuration files)"
    echo "   ├── logs/                    (Execution logs)"
    echo "   └── src/sms_campaign/        (Application code)"
    echo ""

    if [ -f .env ]; then
        # Check if Twilio credentials are configured
        if grep -q "your_account_sid_here" .env 2>/dev/null; then
            echo "⚠️  NEXT STEPS:"
            echo ""
            echo "1. Configure Twilio credentials:"
            echo "   ${YELLOW}nano .env${NC}"
            echo "   (Update TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER)"
            echo ""
        else
            echo "✅ Twilio credentials configured"
            echo ""
        fi
    fi

    echo "2. Update your data files:"
    echo "   - data/customers_list_new.xlsx (your customer list)"
    echo "   - data/campaigns.xlsx (your campaign configuration)"
    echo ""
    echo "3. Test in dry-run mode:"
    echo "   ${GREEN}./run.sh${NC}  or  ${GREEN}uv run python -m sms_campaign.cli${NC}"
    echo ""
    echo "4. When ready, set DRY_RUN=false in .env and run again"
    echo ""
    echo "📚 Documentation:"
    echo "   - README.md        (Complete guide)"
    echo "   - QUICKSTART.md    (Quick start)"
    echo "   - EXAMPLES.md      (Campaign examples)"
    echo ""
    print_success "Setup completed successfully!"
    echo ""
}

# Function to interactively configure Twilio
configure_twilio() {
    echo ""
    print_info "Configuring Twilio credentials..."
    echo ""

    read -p "Enter your Twilio Account SID: " account_sid
    read -p "Enter your Twilio Auth Token: " auth_token
    read -p "Enter your Twilio Phone Number (e.g., +1234567890): " phone_number

    # Update .env file
    sed -i.bak "s|TWILIO_ACCOUNT_SID=.*|TWILIO_ACCOUNT_SID=$account_sid|g" .env
    sed -i.bak "s|TWILIO_AUTH_TOKEN=.*|TWILIO_AUTH_TOKEN=$auth_token|g" .env
    sed -i.bak "s|TWILIO_PHONE_NUMBER=.*|TWILIO_PHONE_NUMBER=$phone_number|g" .env

    # Remove backup file
    rm -f .env.bak

    print_success "Twilio credentials configured"
}

# Function to create helper scripts
create_helper_scripts() {
    # Create run script
    cat > run.sh << 'RUNSCRIPT'
#!/bin/bash
# Quick run script for SMS Campaign Manager
uv run python -m sms_campaign.cli "$@"
RUNSCRIPT
    chmod +x run.sh
    print_success "Created run.sh (Quick run script)"

    # Create test script
    cat > test.sh << 'TESTSCRIPT'
#!/bin/bash
# Test script for SMS Campaign Manager
echo "Running setup verification tests..."
uv run python test_setup.py
TESTSCRIPT
    chmod +x test.sh
    print_success "Created test.sh (Test verification script)"

    # Create update script
    cat > update_data.sh << 'UPDATESCRIPT'
#!/bin/bash
# Helper script to update data files

echo "Current data files:"
echo ""
ls -lh data/*.xlsx data/*.csv 2>/dev/null | grep -v sample || echo "No data files found"
echo ""
echo "To update:"
echo "1. Place your new customer list as: data/customers_list_new.xlsx"
echo "2. Update campaigns in: data/campaigns.xlsx"
echo "3. Run: ./run.sh"
UPDATESCRIPT
    chmod +x update_data.sh
    print_success "Created update_data.sh (Data update helper)"
}

# Run main setup
main

exit 0
