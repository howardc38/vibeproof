## 4. Kernel
<!-- pinned: kernel/ledger.py::SCHEMA -->

Long enough to count as a mechanism section. It describes how the ledger is laid
out and what the append-only triggers do, and it pins the schema so renaming it
fails this check instead of quietly making the paragraph wrong. Padding follows
so the four-hundred-character rule actually fires on this fixture rather than
skipping it as too short to be a mechanism. The `probe` checker is named so the
other-direction rule is satisfied: probe.

### 指令表

| 指令 | 做乜 |
|---|---|
| `v4 audit` | 行 attempt hash chain |

<!-- unbuilt-list -->

| 未起 | 點解 |
|---|---|
| 跨 task 並行 | 押後到停損過咗先起 |

### 收尾

```
./bin/v4 --repo . audit
```
