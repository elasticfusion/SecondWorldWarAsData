#!/bin/bash
# Test runner script with different test modes

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}WWII Data Extraction - Test Suite${NC}\n"

# Parse arguments
MODE=${1:-all}

case $MODE in
    unit)
        echo -e "${YELLOW}Running unit tests only...${NC}"
        pytest tests/unit/ -v
        ;;
    integration)
        echo -e "${YELLOW}Running integration tests only...${NC}"
        pytest tests/integration/ -v
        ;;
    fast)
        echo -e "${YELLOW}Running fast tests (unit only)...${NC}"
        pytest tests/unit/ -v --tb=line
        ;;
    coverage)
        echo -e "${YELLOW}Running tests with coverage report...${NC}"
        pytest --cov=src --cov-report=html --cov-report=term-missing
        echo -e "\n${GREEN}Coverage report generated: htmlcov/index.html${NC}"
        ;;
    watch)
        echo -e "${YELLOW}Running tests in watch mode...${NC}"
        pytest-watch tests/ -- -v
        ;;
    all)
        echo -e "${YELLOW}Running all tests...${NC}"
        pytest tests/ -v
        ;;
    *)
        echo "Usage: ./run_tests.sh [unit|integration|fast|coverage|watch|all]"
        echo ""
        echo "  unit        - Run unit tests only"
        echo "  integration - Run integration tests only"
        echo "  fast        - Run fast tests with minimal output"
        echo "  coverage    - Run tests with coverage report"
        echo "  watch       - Run tests in watch mode (requires pytest-watch)"
        echo "  all         - Run all tests (default)"
        exit 1
        ;;
esac

echo -e "\n${GREEN}✓ Tests complete${NC}"
