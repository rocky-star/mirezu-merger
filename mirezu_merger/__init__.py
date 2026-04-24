"""A script for merging YAML configuration files provided by subscription
services.
"""

__all__ = ['main']
__version__ = '0.1'
__author__ = ('Rocky\N{WHITE STAR}Star <rocky-star22 at outlook dot com>'
              .replace(' at ', '@').replace(' dot ', '.'))

from .cli import main


if __name__ == '__main__':
    main()
