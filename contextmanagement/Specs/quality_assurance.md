# Quality Assurance Tools

**Version**: 1.0.0  
**Last Updated**: 2026-02-24

## Overview

Comprehensive quality assurance checklist organized by programming language. Run these tools before committing code to ensure quality, security, and maintainability.

---

## Python

### Required Tools

#### 1. **Pylint** - Code Quality & Style
```bash
python3 -m pylint <file_or_module>
```

**Purpose**: Static analysis, PEP 8 compliance, code smells  
**Target Score**: ≥ 9.0/10  
**Common Disables**:
- `C0301` - Line too long (if using Black)
- `C0103` - Invalid name (for specific cases)
- `R0913` - Too many arguments (if justified)
- `R0914` - Too many local variables (if justified)
- `R0915` - Too many statements (if justified)
- `W0511` - TODO/FIXME comments
- `R0917` - Too many positional arguments
- `W0718` - Broad exception (if justified)

**Example**:
```bash
python3 -m pylint src/extraction/weather_central.py --disable=C0301,C0103,R0913
```

#### 2. **Mypy** - Type Checking
```bash
python3 -m mypy <file_or_module> --ignore-missing-imports
```

**Purpose**: Static type checking  
**Target**: Zero errors  
**Flags**:
- `--ignore-missing-imports` - Skip third-party type stubs
- `--strict` - Enable all optional checks (optional)

#### 3. **Black** - Code Formatting
```bash
python3 -m black <file_or_module>
```

**Purpose**: Automatic code formatting  
**Target**: All files formatted  
**Config**: Use `pyproject.toml` for line length

**Check without modifying**:
```bash
python3 -m black --check <file_or_module>
```

#### 4. **Bandit** - Security Analysis
```bash
python3 -m bandit -r <file_or_module> -ll
```

**Purpose**: Security vulnerability detection  
**Target**: Zero high/medium issues  
**Flags**:
- `-r` - Recursive
- `-ll` - Only show medium/high severity

#### 5. **Vulture** - Dead Code Detection
```bash
python3 -m vulture <file_or_module>
```

**Purpose**: Find unused code (functions, classes, variables)  
**Target**: Zero unused code (or document false positives)  
**Flags**:
- `--min-confidence 80` - Only show high-confidence findings
- `--exclude` - Exclude directories

**Example**:
```bash
python3 -m vulture src/extraction/ --min-confidence 80
```

**Note**: May report false positives for:
- Functions called dynamically
- Public API methods
- Test fixtures
- Create a whitelist file to suppress known false positives

#### 6. **Radon** - Complexity Analysis
```bash
# Cyclomatic complexity
python3 -m radon cc <file_or_module> -s

# Maintainability index
python3 -m radon mi <file_or_module> -s
```

**Purpose**: Code complexity metrics  
**Targets**:
- Cyclomatic Complexity: A-B (≤6), C acceptable (≤10)
- Maintainability Index: A (≥20)

**Grades**:
- **A**: 1-5 (simple)
- **B**: 6-10 (low complexity)
- **C**: 11-20 (moderate complexity)
- **D**: 21-30 (high complexity)
- **F**: 31+ (very high complexity)

#### 7. **pytest** - Unit Testing
```bash
python3 -m pytest tests/ -v --cov=src --cov-report=term-missing
```

**Purpose**: Run tests with coverage  
**Target**: ≥80% coverage  
**Flags**:
- `-v` - Verbose
- `--cov=<module>` - Coverage for module
- `--cov-report=term-missing` - Show missing lines

### Optional Tools

#### 7. **isort** - Import Sorting
```bash
python3 -m isort <file_or_module>
```

**Purpose**: Sort and organize imports  
**Config**: Compatible with Black

#### 8. **flake8** - Alternative Linter
```bash
python3 -m flake8 <file_or_module>
```

**Purpose**: Style guide enforcement (alternative to pylint)  
**Note**: Less comprehensive than pylint

#### 9. **pydocstyle** - Docstring Checker
```bash
python3 -m pydocstyle <file_or_module>
```

**Purpose**: Validate docstring conventions

---

## Python QA Checklist

Run in this order:

```bash
# 1. Format code
python3 -m black src/extraction/weather_central.py

# 2. Sort imports (optional)
python3 -m isort src/extraction/weather_central.py

# 3. Type checking
python3 -m mypy src/extraction/weather_central.py --ignore-missing-imports

# 4. Code quality
python3 -m pylint src/extraction/weather_central.py --disable=C0301,C0103

# 5. Security scan
python3 -m bandit -r src/extraction/weather_central.py -ll

# 6. Dead code detection
python3 -m vulture src/extraction/weather_central.py --min-confidence 80

# 7. Complexity analysis
python3 -m radon cc src/extraction/weather_central.py -s
python3 -m radon mi src/extraction/weather_central.py -s

# 8. Syntax check
python3 -m py_compile src/extraction/weather_central.py

# 8. Run tests (if available)
python3 -m pytest tests/ -v --cov=src
```

---

## JavaScript/TypeScript

### Required Tools

#### 1. **ESLint** - Linting
```bash
npx eslint <file_or_directory>
```

**Purpose**: Code quality and style  
**Config**: `.eslintrc.js` or `.eslintrc.json`

#### 2. **Prettier** - Formatting
```bash
npx prettier --write <file_or_directory>
```

**Purpose**: Code formatting  
**Config**: `.prettierrc`

#### 3. **TypeScript Compiler** - Type Checking
```bash
npx tsc --noEmit
```

**Purpose**: Type checking (TypeScript only)  
**Target**: Zero errors

#### 4. **Jest** - Testing
```bash
npm test
# or
npx jest --coverage
```

**Purpose**: Unit testing with coverage  
**Target**: ≥80% coverage

### Optional Tools

#### 5. **npm audit** - Security
```bash
npm audit
```

**Purpose**: Dependency vulnerability scanning

#### 6. **Madge** - Circular Dependencies
```bash
npx madge --circular <directory>
```

**Purpose**: Detect circular dependencies

---

## Go

### Required Tools

#### 1. **go fmt** - Formatting
```bash
go fmt ./...
```

**Purpose**: Standard Go formatting

#### 2. **go vet** - Static Analysis
```bash
go vet ./...
```

**Purpose**: Detect suspicious constructs

#### 3. **golangci-lint** - Comprehensive Linting
```bash
golangci-lint run
```

**Purpose**: Multiple linters in one  
**Config**: `.golangci.yml`

#### 4. **go test** - Testing
```bash
go test ./... -cover
```

**Purpose**: Run tests with coverage  
**Target**: ≥80% coverage

### Optional Tools

#### 5. **gosec** - Security
```bash
gosec ./...
```

**Purpose**: Security vulnerability scanning

#### 6. **gocyclo** - Complexity
```bash
gocyclo -over 10 .
```

**Purpose**: Cyclomatic complexity analysis

---

## Rust

### Required Tools

#### 1. **rustfmt** - Formatting
```bash
cargo fmt
```

**Purpose**: Standard Rust formatting

#### 2. **clippy** - Linting
```bash
cargo clippy -- -D warnings
```

**Purpose**: Comprehensive linting  
**Target**: Zero warnings

#### 3. **cargo test** - Testing
```bash
cargo test
```

**Purpose**: Run tests

#### 4. **cargo build** - Compilation
```bash
cargo build --all-features
```

**Purpose**: Ensure code compiles

### Optional Tools

#### 5. **cargo audit** - Security
```bash
cargo audit
```

**Purpose**: Dependency vulnerability scanning

#### 6. **cargo tarpaulin** - Coverage
```bash
cargo tarpaulin --out Html
```

**Purpose**: Code coverage analysis

---

## General Best Practices

### Pre-Commit Checklist

1. ✅ Format code (Black, Prettier, rustfmt, go fmt)
2. ✅ Type check (mypy, tsc)
3. ✅ Lint (pylint, ESLint, clippy, golangci-lint)
4. ✅ Security scan (bandit, npm audit, gosec, cargo audit)
5. ✅ Run tests (pytest, jest, go test, cargo test)
6. ✅ Check complexity (radon, gocyclo)
7. ✅ Verify syntax/compilation

### CI/CD Integration

Add these tools to your CI/CD pipeline:

```yaml
# Example GitHub Actions
- name: Quality Checks
  run: |
    python3 -m black --check .
    python3 -m mypy src --ignore-missing-imports
    python3 -m pylint src --fail-under=9.0
    python3 -m bandit -r src -ll
    python3 -m pytest tests/ --cov=src --cov-fail-under=80
```

### Tool Installation

#### Python
```bash
pip install pylint mypy black bandit radon pytest pytest-cov isort flake8 pydocstyle
```

#### JavaScript/TypeScript
```bash
npm install --save-dev eslint prettier typescript jest @types/jest
```

#### Go
```bash
go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
go install github.com/securego/gosec/v2/cmd/gosec@latest
go install github.com/fzipp/gocyclo/cmd/gocyclo@latest
```

#### Rust
```bash
rustup component add rustfmt clippy
cargo install cargo-audit cargo-tarpaulin
```

---

## Quality Metrics

### Target Scores

| Metric | Target | Tool |
|--------|--------|------|
| Pylint Score | ≥ 9.0/10 | pylint |
| Type Errors | 0 | mypy, tsc |
| Security Issues | 0 high/medium | bandit, gosec, npm audit |
| Test Coverage | ≥ 80% | pytest, jest, go test |
| Cyclomatic Complexity | A-B (≤10) | radon, gocyclo |
| Maintainability Index | A (≥20) | radon |

### Acceptable Exceptions

- **Complexity C (11-20)**: Acceptable for error handling, retry logic, validation
- **Broad exceptions**: Acceptable with proper logging and re-raising
- **Line length**: Acceptable if using auto-formatter (Black, Prettier)
- **Too many arguments**: Acceptable for configuration functions

---

## Version History

- **1.0.0** (2026-02-24): Initial specification with Python, JavaScript/TypeScript, Go, Rust
