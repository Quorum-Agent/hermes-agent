"""Software taxonomy — Python port of packages/core/src/software-taxonomy.ts.

Deterministic vocabulary used by the compiler as a safety net around any
prompt expert. Values verbatim from the TS source.
"""

from __future__ import annotations

import re

SOFTWARE_TERMS = {
    "languages": [
        "c#",
        "c++",
        "clojure",
        "cobol",
        "erlang",
        "f#",
        "fortran",
        "gdscript",
        "golang",
        "haskell",
        "html",
        "javascript",
        "json",
        "jsx",
        "julia lang",
        "kotlin",
        "micropython",
        "matlab",
        "nim lang",
        "ocaml",
        "objective-c",
        "odin lang",
        "php",
        "powershell",
        "prolog",
        "typescript",
        "xml",
        "visual basic",
        "visual basic .net",
        "vba",
        "vb.net",
        "wasm",
        "webassembly",
    ],
    "webAndApplication": [
        "asp.net",
        "aws lambda",
        "css",
        "cuda",
        "deno",
        "django",
        "express.js",
        "expressjs",
        "fastapi",
        "htmx",
        "jquery",
        "keras",
        "laravel",
        "matplotlib",
        "nestjs",
        "next.js",
        "nextjs",
        "node.js",
        "nodejs",
        "nuxt.js",
        "nuxt",
        "nuxtjs",
        "react native",
        "rxjs",
        "ruby on rails",
        "scikit-learn",
        "scipy",
        "scss",
        "solidjs",
        "spring boot",
        "spring framework",
        "symfony",
        "tailwind css",
        "tensorflow",
        "pytorch",
        "vue.js",
        "vuejs",
        "wordpress",
    ],
    "data": [
        "bigquery",
        "couchbase",
        "couchdb",
        "clickhouse",
        "cosmos db",
        "db2",
        "duckdb",
        "dynamodb",
        "elasticsearch",
        "firebase",
        "mariadb",
        "mongodb",
        "ms sql",
        "mssql",
        "mysql",
        "neo4j",
        "nosql",
        "opensearch",
        "pl/sql",
        "postgres",
        "postgresql",
        "redis",
        "snowflake db",
        "sql",
        "sqlite",
        "sql server",
        "supabase",
        "t-sql",
        "timescaledb",
    ],
    "tooling": [
        "ansible",
        "circleci",
        "ci/cd",
        "cmake",
        "dockerfile",
        "eslint",
        "github",
        "github actions",
        "gitlab",
        "gitlab ci",
        "graphql",
        "gradle",
        "junit",
        "k8s",
        "kubernetes",
        "makefile",
        "meson build",
        "nuget",
        "npm",
        "pnpm",
        "pytest",
        "regular expression",
        "regex",
        "rollup.js",
        "intellij idea",
        "visual studio code",
        "vscode",
        "vitest",
        "webpack",
        "yaml",
    ],
    "artifacts": [
        "cargo.toml",
        "composer.json",
        "package.json",
        "pom.xml",
        "pyproject.toml",
        "requirements.txt",
        "tsconfig.json",
    ],
}

CONTEXTUAL_SOFTWARE_TERMS = [
    ".net",
    "angular",
    "android",
    "assembly",
    "astro",
    "babel",
    "bash",
    "bootstrap",
    "bun",
    "c",
    "cargo",
    "cassandra",
    "cypress",
    "dart",
    "delphi",
    "docker",
    "electron",
    "elixir",
    "express",
    "flask",
    "flutter",
    "git",
    "go",
    "gleam",
    "groovy",
    "helm",
    "hcl",
    "java",
    "jax",
    "jenkins",
    "jest",
    "js",
    "julia",
    "lua",
    "maven",
    "mocha",
    "nix",
    "node",
    "numpy",
    "oracle",
    "pandas",
    "perl",
    "phoenix",
    "pip",
    "playwright",
    "poetry",
    "prettier",
    "python",
    "qt",
    "r",
    "rails",
    "react",
    "redux",
    "remix",
    "ruby",
    "rust",
    "sass",
    "scala",
    "selenium",
    "shell",
    "solidity",
    "spring",
    "svelte",
    "swift",
    "terraform",
    "ts",
    "tsx",
    "unity",
    "unreal",
    "vite",
    "vue",
    "yarn",
    "zig",
]

CONTEXTUAL_SOFTWARE_PHRASES = [
    "android activity",
    "android app",
    "angular component",
    "angular module",
    "bash script",
    "docker compose",
    "docker container",
    "docker image",
    "express app",
    "flask app",
    "flutter widget",
    "git branch",
    "git commit",
    "git merge",
    "git rebase",
    "git repository",
    "go method",
    "go module",
    "go package",
    "hcl config",
    "hcl module",
    "helm chart",
    "java package",
    "jest test",
    "js app",
    "js application",
    "js applications",
    "oracle database",
    "python module",
    "python package",
    "python script",
    "rails app",
    "react component",
    "react hook",
    "react state",
    "redux reducer",
    "ruby method",
    "sass stylesheet",
    "rust crate",
    "rust module",
    "spring controller",
    "svelte component",
    "terraform module",
    "terraform plan",
    "terraform provider",
    "terraform state",
    "ts service",
    "unreal engine",
    "vue component",
]

MAX_NAMED_SOFTWARE_FOLLOW_UP_LENGTH = 240


def _escape_term(term: str) -> str:
    out = re.sub(r"([.*+?^${}()|[\]\\])", r"\\\1", term)
    return re.sub(r"\s+", r"\\s+", out)


def _term_source(terms) -> str:
    alternatives = "|".join(
        _escape_term(t) for t in sorted(terms, key=len, reverse=True)
    )
    # JS uses (?<![\p{L}\p{N}_])(?:...)(?![\p{L}\p{N}_]) — a Unicode-aware word
    # boundary. Python re has no \p escapes, so we use \b, which is already
    # Unicode-aware (matches between \w = letter/digit/underscore and non-word).
    # Every term begins and ends with a letter or digit, so \b...\b is faithful.
    return rf"\b(?:{alternatives})\b"


_strong_flat = [t for v in SOFTWARE_TERMS.values() for t in v]
STRONG_SOFTWARE_PATTERN = re.compile(_term_source(_strong_flat), re.I | re.U)
_CONTEXTUAL_TERM_SOURCE = _term_source(CONTEXTUAL_SOFTWARE_TERMS)
_ANY_SOFTWARE_TERM_SOURCE = _term_source(_strong_flat + CONTEXTUAL_SOFTWARE_TERMS)
CONTEXTUAL_SOFTWARE_PHRASE_PATTERN = re.compile(
    _term_source(CONTEXTUAL_SOFTWARE_PHRASES), re.I | re.U
)
CONTEXTUAL_CODE_PATTERN = re.compile(
    rf"{_CONTEXTUAL_TERM_SOURCE}(?:[-\s]+(?:based|powered))?[-\s]+(?:code|codebases?|programming|runtimes?|sdks?|source)\b",
    re.I | re.U,
)
NAMED_SOFTWARE_FOLLOW_UP_PATTERN = re.compile(
    rf"^\s*(?:(?:what|how)\s+about\s+(?:using\s+)?(?:the\s+)?{_ANY_SOFTWARE_TERM_SOURCE}(?:\s+(?:instead|for\s+(?:this|that|it)))?|"
    rf"(?:would|could|can|does|is|are)\s+(?:the\s+)?{_ANY_SOFTWARE_TERM_SOURCE}(?:\s+(?:be(?:\s+used)?|work|behave|perform|handle))?(?:\s+(?:any\s+)?(?:better|different|equivalent|instead|here|this|that|it|the\s+same\s+way|for\s+(?:this|that|it)))*|"
    rf"(?:could|can|would|should)\s+(?:we|i|you)\s+(?:use|try|choose|switch\s+to)\s+(?:the\s+)?{_ANY_SOFTWARE_TERM_SOURCE}(?:\s+instead)?|"
    rf"(?:the\s+)?{_ANY_SOFTWARE_TERM_SOURCE}(?:\s+(?:instead|for\s+(?:this|that|it)))?)\s*\??\s*$",
    re.I | re.U,
)


def detect_software_reference(prompt: str) -> str | None:
    """Return 'strong' | 'contextual' | None."""
    if STRONG_SOFTWARE_PATTERN.search(prompt):
        return "strong"
    if CONTEXTUAL_SOFTWARE_PHRASE_PATTERN.search(
        prompt
    ) or CONTEXTUAL_CODE_PATTERN.search(prompt):
        return "contextual"
    return None


def is_named_software_follow_up(prompt: str) -> bool:
    if len(prompt) > MAX_NAMED_SOFTWARE_FOLLOW_UP_LENGTH:
        return False
    return bool(
        NAMED_SOFTWARE_FOLLOW_UP_PATTERN.search(re.sub(r"\s+", " ", prompt.strip()))
    )
