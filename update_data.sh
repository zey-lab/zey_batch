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
