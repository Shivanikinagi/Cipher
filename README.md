Overview
Cipher builds a complete dependency map of your codebase and helps AI coding assistants understand what will break before making changes.

Key concept: When an AI deletes a file, Cipher knows which 30 files depend on it and warns the AI before the change happens.

Installation
bash
npm install -g cipher-cli
Requires Node.js 18+.

Quick Start
bash
# Map your codebase
cipher parse .

# Check what breaks before deleting
cipher whatif . --simulate delete --target src/helpers.ts

# Scan for security issues
cipher security .

# View architecture health
cipher health .
Commands
Command	Description
cipher whatif	Simulate delete/move/rename and see impact
cipher security	Scan for vulnerabilities
cipher health	Get architecture score (0-100)
cipher dead-code	Find unused code
cipher viz	Visual dependency map
cipher diff	Compare commits
cipher verify-change	Check if a change is safe
Example
Before deleting a file:

bash
cipher whatif . --simulate delete --target src/auth.ts
Output:

text
Health Score: 67 → 63 (-4)
Affected Files: 12
Broken Imports: 15
  • src/server.ts imports verifyToken
  • src/api/users.ts imports hashPassword
  • src/middleware/auth.ts imports validateSession
  [12 more files...]
AI Integration (MCP)
Cipher provides 24 tools for AI assistants:

get_file_context - What depends on this file?

impact_analysis - What breaks if I change this?

security_scan - Find vulnerabilities

simulate_change - Test changes before applying

Connect to Claude Desktop:

json
{
  "mcpServers": {
    "cipher": {
      "command": "npx",
      "args": ["-y", "cipher-cli", "mcp"]
    }
  }
}
Supported Languages
TypeScript, JavaScript, Python, Go, Rust, C, C#, Java, C++, Kotlin, PHP, Swift, Ruby, Dart, R, Mojo, HTML/Angular

Benchmarks
80-file refactor on a large TypeScript codebase:

Mode	Time	Cost
Without Cipher	16m 46s	$9.03
With Cipher	10m 43s	$7.35
36% faster. 19% cheaper.

Documentation
Full Documentation

MCP Integration

API Reference

Contributing
Fork the repo

Create a feature branch

Submit a pull request

See CONTRIBUTING.md for details.
