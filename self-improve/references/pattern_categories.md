# Pattern Categories Reference

When analyzing user steering patterns, classify them into these categories. Each category includes common signals and example preferences.

## 1. Communication Style

How the user wants Claude to communicate.

**Signals to watch for:**
- "Too long", "be brief", "just give me the answer", "stop explaining"
- "Give me more detail", "explain why", "walk me through it"
- "Don't use bullet points", "use headers", "format as a table"
- "Less formal", "be more professional", "talk to me like a colleague"
- "Don't ask me questions", "just do it", "stop checking with me"

**Example preferences:**
- Keep responses under 3 paragraphs unless asked for detail
- Use prose, not bullet points, for explanations
- Skip preambles — start with the answer
- Don't hedge or qualify — be direct and confident
- Use technical jargon freely (user is an expert)
- Always explain the "why" behind suggestions

## 2. Technical Preferences

Languages, tools, frameworks, and technical choices.

**Signals to watch for:**
- "Use TypeScript not JavaScript", "I prefer Rust", "we use Go here"
- "Don't use X library", "we use Y for that", "our stack is..."
- "Use functional style", "prefer composition over inheritance"
- "We use pnpm not npm", "yarn, not npm", "bun"
- "Use PostgreSQL", "we're on Redis", "DynamoDB"

**Example preferences:**
- Default to TypeScript with strict mode for all JS projects
- Use pnpm as the package manager
- Prefer functional patterns over OOP
- Use Tailwind CSS, not styled-components
- Default to PostgreSQL for database suggestions

## 3. Workflow Preferences

How the user wants to work with Claude — level of autonomy, planning depth, iteration style.

**Signals to watch for:**
- "Just do it, don't ask" vs. "Check with me first"
- "Plan before you code" vs. "Just start coding"
- "Do it all at once" vs. "One step at a time"
- "Don't show me the code, just run it" vs. "Let me review first"
- "Skip the tests for now" vs. "Always write tests"

**Example preferences:**
- High autonomy — make decisions without asking unless truly ambiguous
- Always plan before implementing (use plan mode)
- Show diffs instead of full files
- Commit after each meaningful change, not in bulk
- Run tests automatically after code changes

## 4. Code Style

Formatting, architecture, naming, and code organization preferences.

**Signals to watch for:**
- "2 spaces not 4", "use tabs", "single quotes"
- "That function is too long, break it up", "keep it in one file"
- "Use descriptive names", "that variable name is too long"
- "Add comments", "the code should be self-documenting"
- "Use early returns", "avoid nested ifs"

**Example preferences:**
- 2-space indentation, single quotes, no semicolons (JS/TS)
- Prefer early returns over nested conditionals
- Keep functions under 30 lines
- Use descriptive variable names (no abbreviations)
- Colocate tests with source files (not in separate test/ directory)
- Prefer named exports over default exports

## 5. Research & Analysis Approach

How the user wants Claude to research, analyze, and present findings.

**Signals to watch for:**
- "Don't search the web, use your knowledge"
- "Always check the docs first", "look at the source code"
- "Give me options, not a single recommendation"
- "Just tell me the best one", "don't give me 5 options"
- "Show me the evidence", "cite your sources"

**Example preferences:**
- Search documentation before answering technical questions
- Present one recommended option with brief rationale, not a list
- Always read existing code patterns before suggesting new code
- Cite sources when making factual claims

## 6. Decision Making & Autonomy

When to ask vs. decide, how to present choices.

**Signals to watch for:**
- Repeated "just pick one" after Claude presents options
- "Stop asking and do it" — user wants higher autonomy
- "Wait, why did you do X?" — user wants more check-ins
- "I would have preferred if you asked" — user wants lower autonomy

**Example preferences:**
- Make architectural decisions without asking if there's a clear best practice
- Always ask before deleting files or making breaking changes
- When presenting options, recommend one and explain why
- Default to the simpler approach unless complexity is justified

## 7. Error Handling & Debugging

How the user prefers Claude to handle errors and debugging.

**Signals to watch for:**
- "Don't just fix it, explain what was wrong"
- "Just fix it, I don't need the explanation"
- "Try a different approach" (instead of fixing the current one)
- "Read the error message more carefully"

**Example preferences:**
- When hitting errors, read the full stack trace before attempting fixes
- Try at most 2 approaches before asking the user for direction
- Explain what caused the error before showing the fix
- Don't silently swallow errors — always surface them

## 8. Context & Domain

Project-specific or domain-specific patterns that should persist.

**Signals to watch for:**
- Repeated corrections about domain terminology
- "In our codebase, X means Y"
- "Our users are [specific audience]"
- "We follow [specific methodology]"

**Example preferences:**
- "Customer" means enterprise B2B clients, not end-users
- Follow the team's ADR process for architectural decisions
- Use the company's API naming conventions (camelCase, /v1/ prefix)
