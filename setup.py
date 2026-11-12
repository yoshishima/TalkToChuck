import setuptools

with open('README.md', 'r', encoding='utf-8') as f:
    long_description = f.read()

with open('schwab/version.py', 'r') as f:
    '''Version looks like `version = '1.2.3'`'''
    version = [s.strip() for s in f.read().strip().split('=')][1]
    version = version[1:-1]

setuptools.setup(
    name='schwab-py',
    version=version,
    author='Alex Golec',
    author_email='bottomless.septic.tank@gmail.com',
    description='Unofficial API wrapper for the Schwab HTTP API',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/yoshishima/schwab-py',
    packages=setuptools.find_packages(exclude=('tests', 'tests.*')),
    package_data={'schwab': ['py.typed']},
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Programming Language :: Python :: 3.14',
        'Operating System :: OS Independent',
        'Intended Audience :: Developers',
        'Development Status :: 4 - Beta',
        'Natural Language :: English',
        'Topic :: Office/Business :: Financial :: Investment',
    ],
    python_requires='>=3.12',
    # Keep the floors in docs/requirements.txt in sync; tests/audit_fixes_test.py
    # checks that they agree. httpx2 uses the operating system's trust store
    # (via truststore), so certifi is not needed. websockets 17.3 fixes recv()
    # cancellation; 17.4 also fixes fragmented-message memory exhaustion.
    install_requires=[
        'autopep8>=2.3.2',
        'authlib>=1.9.5',
        'flask>=3.1.3',
        'httpx2>=2.16.0',
        'multiprocess>=0.70.19',
        'psutil>=7.2.2',
        'websockets>=17.4'
    ],
    extras_require={
        'dev': [
            'callee',
            'colorama',
            'coverage',
            'build',
            'flake8',
            'pytest',
            'pytz',
            'requests',
            'setuptools',
            'sphinx_rtd_theme',
            'twine',
            'tox',
            'wheel',
        ]
    },
    keywords='finance trading equities bonds options research',
    project_urls={
        'Documentation': 'https://schwab-py.readthedocs.io/en/latest/',
        'Source': 'https://github.com/yoshishima/schwab-py',
        'Tracker': 'https://github.com/yoshishima/schwab-py/issues',
    },
    license='MIT',
    scripts=[
        'bin/schwab-order-codegen.py',
        'bin/schwab-generate-token.py',
    ],
)
