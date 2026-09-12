"""A URL or a quote in prose must not change which later code is visible."""
from pathlib import Path
import tempfile
import unittest

from kernel.analysis import symbols, facts_grammar, test_weakened, test_token_shape
from kernel.analysis.test_expectation import _ts_mask


class LexicalBoundaries(unittest.TestCase):
    TESTS = ''.join(f'test("case {i}", () => {{ expect({i}).toBe({i}); }});\n'
                    for i in range(8))

    def test_urls_and_comment_delimiters_in_literals_do_not_hide_tests(self):
        for literal in ['"https://example.com"', "'/* literal */ // text'",
                        '`https://example.com\n/* still text */`', '"a\\\"//b"']:
            with self.subTest(literal=literal):
                self.assertEqual(test_weakened.ts_count('const value = '+literal+';\n'+self.TESTS), 8)

    def test_quotes_in_comments_do_not_consume_code_or_create_tests(self):
        for comment in ["// don't pair this quote with the next line\n",
                        '/* " fake test("x", () => { boom(); }); */\n',
                        '// `unfinished template in prose\n']:
            with self.subTest(comment=comment):
                self.assertEqual(test_weakened.ts_count(comment+self.TESTS), 8)

    def test_strings_comments_skips_and_empty_bodies_are_still_not_live_tests(self):
        src = ('const example = `test("fake", () => { boom(); });`;\n'
               '// test("fake", () => { boom(); });\n'
               'it.skip("skip", () => { boom(); });\n'
               'test("empty", () => {});\n'+self.TESTS)
        self.assertEqual(test_weakened.ts_count(src), 8)

    def test_mask_keeps_offsets_and_lines_and_leaves_later_calls_visible(self):
        src = 'const url = "https://example.com"; client.send(url);\n/* quote "\n */ next();\n'
        masked = _ts_mask(src)
        self.assertEqual(len(src), len(masked))
        self.assertEqual([i for i,c in enumerate(src) if c=='\n'],
                         [i for i,c in enumerate(masked) if c=='\n'])
        self.assertEqual(masked.index('client.send'), src.index('client.send'))
        self.assertNotIn('example.com', masked)
        self.assertIn('next();', masked)

    def test_symbol_readers_preserve_real_imports_but_ignore_quoted_declarations(self):
        src = ('const url = "https://example.com";\n'
               'const sample = `\nexport const Fake = 1;\nimport {Fake} from "./fake";\n`;\n'
               '// a stray quote "\nimport {Real} from "./real";\nexport const Good = 1;\n')
        self.assertEqual(symbols.ts_imports(src), [('./real', ['Real'], 7)])
        self.assertEqual(symbols.ts_exported_names(src), {'Good'})
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'module.ts';p.write_text(src)
            names=symbols.names_in(p)
            self.assertIn('Good', names)
            self.assertNotIn('Fake', names)

    def test_facts_and_literal_consumers_use_the_same_boundaries(self):
        src = 'const url = "https://example.com"; client.send(url); // "quoted prose\n'
        self.assertIn(('client.send', 1), list(facts_grammar.ts_dotted_names(src)))
        self.assertIn('https://example.com', facts_grammar._ts_regex_lines(src)[1])
        self.assertNotIn('quoted prose', facts_grammar._ts_regex_lines(src)[1])
        self.assertIn('https://example.com', test_token_shape._ts_comments_blanked(src))


if __name__ == '__main__':
    unittest.main()
