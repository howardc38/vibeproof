"""Source-reading setup and product behavior must not become text-only proof."""
import unittest
from kernel.analysis.test_shape import ts_findings


class SourceReaderFlow(unittest.TestCase):
    def findings(self, source):
        return ts_findings('test.cjs',source,True,'source_assertion')

    def test_qualified_reader_and_cjs_are_checked(self):
        self.assertTrue(self.findings("const src=fs.readFileSync('app.cjs','utf8'); assert.ok(src.includes('guard'));"))

    def test_data_json_readback_does_not_masquerade_as_js_source(self):
        self.assertFalse(self.findings("const data=fs.readFileSync('state.json','utf8'); assert.equal(data,'expected state');"))

    def test_local_path_and_value_aliases_reach_both_assertion_arguments(self):
        for assertion in ['expect(code).toContain("guard")','assert.match(code,/guard/)',
                          'assert.equal("expected",code)','expect("expected").toEqual(code)']:
            with self.subTest(assertion=assertion):
                self.assertTrue(self.findings("const file='app.ts'; const src=fs.readFileSync(file,'utf8'); const code=src; "+assertion))

    def test_distinct_test_scopes_do_not_share_same_named_state(self):
        source="test('setup',()=>{const src=fs.readFileSync('app.js','utf8'); writeMutant(src);});\n"
        source+="test('readback',()=>{const src=getActualState(); assert.equal(src,'saved');});"
        self.assertFalse(self.findings(source))

    def test_source_used_to_build_mutant_is_not_the_assertion_oracle(self):
        source="const src=fs.readFileSync('app.js','utf8'); fs.writeFileSync(mutant,src.replace(before,after)); const result=run(mutant); assert.equal(result.status,1);"
        self.assertFalse(self.findings(source))

    def test_asserting_direct_read_or_handwritten_predicate_is_not_hidden(self):
        for source in ["assert.match(fs.readFileSync('app.mjs','utf8'),/guard/);",
                       "const src=fs.readFileSync('app.mts','utf8'); if(!src.includes('guard')) throw Error('missing');"]:
            self.assertTrue(self.findings(source))
