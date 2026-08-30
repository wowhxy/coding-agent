# Two-Minute Plugin Demo

This demo installs the bundled `git-readonly` example as trusted local code,
enables it explicitly, uses plugin and built-in tools in one normal Agent Loop,
then disables and removes it. Plugins execute in the coding-agent process; this
is not an OS sandbox. Only install code you have inspected and trust.

## 1. Prepare an isolated plugin home

Run these commands from the coding-agent repository in PowerShell. The explicit
temporary home makes the demo repeatable and keeps normal sessions untouched.

```powershell
$agentHome = Join-Path $env:TEMP "coding-agent-plugin-demo"
$env:CODING_AGENT_HOME = $agentHome
$pluginRoot = Join-Path $agentHome "plugins"
New-Item -ItemType Directory -Force -Path $pluginRoot | Out-Null
Copy-Item -LiteralPath ".\examples\plugins\git-readonly" -Destination $pluginRoot -Recurse
```

Set the current directory to a small Git workspace with an uncommitted source
change. Do not put credentials in that workspace, prompt, or repository.

```powershell
Set-Location "D:\path\to\demo-workspace"
python -m coding_agent --provider deepseek
```

The API key prompt is hidden if `DEEPSEEK_API_KEY` is not already set.

## 2. Run the interactive trace

Enter these lines one at a time:

```text
/plugins
/plugin enable git-readonly
/plugins
Use git_status and git_diff to inspect the current change, then use read_file to explain the modified source. Do not edit files.
/plugin disable git-readonly
/plugins
/exit
```

Expected evidence:

- The first list shows `git-readonly` as disabled, then enabled, then disabled.
- The agent trace requests `git_status`, `git_diff`, and the built-in `read_file`.
- Disabling removes all three plugin tool definitions from later model turns.
- Restarting while a plugin remains enabled restores it from `plugins.json`.

The example adds value beyond `execute_command`: each operation has a narrow
JSON schema, uses a fixed argv with `shell=False`, rejects arbitrary Git
arguments and unsafe paths, has bounded timeout/output, and exposes no mutation
command. It is a constrained interface, not a security boundary.

Plugin tools are serial by default. The bundled Git tools explicitly declare
`READ_ONLY` and `parallel_safe=True`, so one model turn may run them concurrently
with other explicitly safe reads. Mutating, command, control, and unclassified
Plugin tools remain serial barriers; names such as `git_status` are never used to
guess safety.

## 3. Cleanup

The following removes only the explicit temporary home created in step 1:

```powershell
Remove-Item -LiteralPath $agentHome -Recurse -Force
Remove-Item Env:CODING_AGENT_HOME
```
