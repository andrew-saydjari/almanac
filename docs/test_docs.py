#!/usr/bin/env python
"""
Test script to validate documentation setup and build.

This script performs basic validation of the documentation configuration
and can be used to test builds locally before deploying to ReadTheDocs.
"""

import os
import sys
import subprocess
from pathlib import Path


def check_file_exists(filepath, description):
    """Check if a file exists and report status."""
    if Path(filepath).exists():
        print(f"✓ {description}: {filepath}")
        return True
    else:
        print(f"✗ {description}: {filepath} (MISSING)")
        return False


def check_sphinx_build():
    """Test if Sphinx can build the documentation."""
    docs_dir = Path(__file__).parent
    build_dir = docs_dir / "_build" / "test"
    
    try:
        # Clean previous test build
        if build_dir.exists():
            import shutil
            shutil.rmtree(build_dir)
        
        # Attempt to build
        cmd = [
            "sphinx-build", 
            "-b", "html",
            "-W",  # Treat warnings as errors
            str(docs_dir),
            str(build_dir)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=docs_dir)
        
        if result.returncode == 0:
            print("✓ Sphinx build successful")
            return True
        else:
            print("✗ Sphinx build failed:")
            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
            return False
            
    except FileNotFoundError:
        print("✗ Sphinx not found - install with: pip install sphinx")
        return False
    except Exception as e:
        print(f"✗ Sphinx build error: {e}")
        return False


def check_requirements():
    """Check if documentation requirements are satisfied."""
    requirements_file = Path(__file__).parent / "requirements.txt"
    
    if not requirements_file.exists():
        print("✗ docs/requirements.txt not found")
        return False
    
    try:
        with open(requirements_file) as f:
            requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
        print(f"✓ Found {len(requirements)} requirements in requirements.txt")
        
        # Try to import key packages
        import_tests = [
            ('sphinx', 'Sphinx'),
            ('furo', 'furo theme (html_theme in conf.py)'),
        ]
        
        success = True
        for module, description in import_tests:
            try:
                __import__(module)
                print(f"✓ {description} available")
            except ImportError:
                print(f"✗ {description} not available - install requirements.txt")
                success = False
        
        return success
        
    except Exception as e:
        print(f"✗ Error checking requirements: {e}")
        return False


def check_package_import():
    """Check if almanac package can be imported."""
    try:
        import almanac
        print(f"✓ almanac package imported successfully (version: {almanac.__version__})")
        return True
    except ImportError as e:
        print(f"✗ Cannot import almanac package: {e}")
        print("  Install with: pip install -e .")
        return False


def main():
    """Run all documentation validation checks."""
    print("almanac Documentation Validation")
    print("=" * 40)
    
    docs_dir = Path(__file__).parent
    os.chdir(docs_dir)
    
    checks = []
    
    # File existence checks
    files_to_check = [
        ("conf.py", "Sphinx configuration"),
        ("index.rst", "Main documentation index"),
        ("requirements.txt", "Documentation requirements"),
        ("Makefile", "Sphinx Makefile"),
        ("../pyproject.toml", "Project configuration"),
        ("../.readthedocs.yaml", "ReadTheDocs configuration"),
    ]
    
    print("\n📁 File Existence Checks:")
    for filepath, description in files_to_check:
        checks.append(check_file_exists(filepath, description))
    
    print("\n📦 Package Import Check:")
    checks.append(check_package_import())
    
    print("\n📚 Requirements Check:")
    checks.append(check_requirements())
    
    print("\n🏗️  Sphinx Build Test:")
    checks.append(check_sphinx_build())
    
    # Summary
    print("\n" + "=" * 40)
    passed = sum(checks)
    total = len(checks)
    
    if passed == total:
        print(f"✅ All {total} checks passed! Documentation setup is ready.")
        return 0
    else:
        print(f"❌ {total - passed} checks failed out of {total} total.")
        print("\nFix the issues above before deploying to ReadTheDocs.")
        return 1


if __name__ == "__main__":
    sys.exit(main())