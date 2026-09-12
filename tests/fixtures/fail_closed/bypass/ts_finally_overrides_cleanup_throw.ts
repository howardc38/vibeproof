function save() { try { fs.writeFileSync(file, data); } catch (primary) { try { fs.unlinkSync(tmp); } catch {} throw primary; } finally { return true; } }
