# WidgetFlow

WidgetFlow runs YAML-defined jobs and writes a log for each step.

Use it when you need a small workflow runner that can retry failed shell commands and stop on missing environment variables.

## Quick start

```bash
widgetflow run config.yml
```

## What it does

- Reads jobs from `config.yml`.
- Runs each step in order.
- Retries failed steps up to the limit you set.
- Writes `widgetflow.log` with the command, exit code, and duration.

## Limits

WidgetFlow is not a distributed scheduler. It runs one workflow on one machine.
