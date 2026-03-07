# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mirezu Merger is a Python script for merging YAML configuration files from subscription services (primarily for proxy/VPN configurations like Clash). It merges nodes, node groups, and routing rules from multiple subscriptions, generating different variants for various devices.

## Build System and Development

### Package Management
- Uses Hatch as the build system (pyproject.toml)
- Dependencies: `ruamel.yaml`, `typing-extensions >= 4.15, < 5`
- Development dependencies: `isort` (in dependency group `dev`)
- Python version: >= 3.10

### Building and Installation
```bash
# Install in development mode
pip install -e .

# Build the package
hatch build

# Clean build artifacts
hatch clean
```

### Internationalization (i18n)
- Uses gettext for translations
- PO files in `mirezu_merger/locale/`
- Custom Hatch build hook (`hatch_build.py`) compiles .po to .mo files during build
- Update translation templates:
  ```bash
  python utils/update-pot.py  # Generate .pot file
  python utils/update-po.py   # Update .po files from .pot
  ```

### Type Checking
- Uses Pyright (configured in pyproject.toml)
- Custom type stubs in `typings/` for gettext functions
- Python version: 3.10, platform: All

### Code Quality
- Uses Ruff for linting (configured in pyproject.toml)
- Builtins include `_` and `ngettext` for gettext

## Architecture

### Core Components

1. **Configuration Types** (`mirezu_merger/__init__.py`):
   - `Config`: Main TOML configuration structure
   - `SubscriptionConfig`: Individual subscription settings
   - `SubscriptionMappingConfig`: Mapping between source and target proxy groups
   - `ClashRoot`, `ClashProxy`, `ClashProxyGroup`: YAML structure types for Clash configs

2. **Main Workflow** (`main()` function):
   - Loads TOML configuration file
   - Loads YAML template file
   - Retrieves subscription configurations via HTTP
   - Merges proxies and proxy groups
   - Applies profile patches
   - Writes output files

3. **Key Functions**:
   - `merge_subscription_proxies()`: Merges individual proxy nodes
   - `merge_subscription_proxy_groups()`: Merges proxy group mappings
   - `make_request()`: Handles HTTP requests with proxy support
   - `apply_patch()`: Applies profile patches to templates
   - `install_translation()`: Sets up gettext translations

### HTTP and Proxy Support
- Custom `URLQuotingFormatter` for URL template formatting
- Supports both standard and URL-based proxy configurations
- User-Agent defaults to 'clash-verge/1.19.4'

### Internationalization System
- Locale detection via `locale.getlocale()`
- Translation files compiled to .mo during build
- Uses `_()` and `ngettext()` for string translation
- Chinese (zh_CN) translation available

## Common Development Tasks

### Running the Application
```bash
# Basic usage
mirezu-merger config.toml profiles/ -o output/

# With verbose logging
mirezu-merger config.toml profiles/ -v -v

# Specify locale
mirezu-merger config.toml profiles/ --locale zh_CN
```

### Testing Changes
```bash
# Type checking
pyright mirezu_merger/

# Linting
ruff check mirezu_merger/

# Import sorting
isort mirezu_merger/
```

### Documentation
- User manual in Chinese: `docs/manual.zh-cn.tex`
- Built to PDF via GitHub Actions workflow (`.github/workflows/uplatex-user-manual.yml`)
- README available in English (`README.rst`) and Chinese (`README.zh-cn.rst`)

## Project Structure

```
mirezu-merger/
├── mirezu_merger/          # Main Python package
│   ├── __init__.py        # Entire application (single module)
│   └── locale/            # Translation files (.po, .mo)
├── docs/                  # Documentation (LaTeX)
├── typings/              # Type stubs for gettext
├── utils/                # Utility scripts for i18n
├── hatch_build.py        # Custom Hatch build hook
└── pyproject.toml        # Build configuration
```

## Important Notes

- The entire application is in a single module (`mirezu_merger/__init__.py`)
- Uses `typing_extensions` for advanced type hints (`NotRequired`, `assert_never`, `override`)
- Configuration files are TOML, template/output files are YAML
- Built-in proxy names: `DIRECT`, `REJECT`, `REJECT-DROP`, `PASS`, `COMPATIBLE`
- Error handling uses logging with translation support
- Exit code 1 indicates failure