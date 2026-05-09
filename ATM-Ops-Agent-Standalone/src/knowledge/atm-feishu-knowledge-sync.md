# ATM Feishu Knowledge Sync Policy

## Default Requirement

For future ATM operations work:

- detailed operation logs
- inspection results
- root-cause analysis
- repair actions
- validation results
- command alignment corrections

must be archived as structured knowledge and prepared for Feishu knowledge-base updates.

## Standard Output Structure

Each operation record should contain:

1. incident summary
2. affected servers and systems
3. timeline
4. commands executed
5. key outputs and findings
6. root cause
7. repair or rollback actions
8. validation results
9. follow-up recommendations
10. reusable SOP or command alignment notes

## Sync Strategy

### Current Stage

- write structured local knowledge first
- keep files searchable in the repository knowledge area
- reuse them for later troubleshooting and agent recall

### Target Stage

- connect Feishu/Lark app credentials
- map one or more target Feishu documents or wiki nodes
- update the target knowledge pages after each completed ops task

## Implementation Note

Current repository capability:

- supported: import Feishu documents into local knowledge
- not yet supported: create/update Feishu documents from local ops results

To fully enable write-back, extend `src/ops_agent/feishu.py` with:

- create document
- update raw content
- append/update knowledge page sections
- optional wiki node routing

## Operator Rule

When the user asks to "record", "沉淀", "归档", "同步飞书", or "更新知识库":

- generate the full structured ops record
- preserve command details and validation evidence
- prepare content in a Feishu-friendly markdown structure
- if write-back is not yet enabled, save locally and clearly mark it as pending Feishu sync
