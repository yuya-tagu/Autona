/**
 * Contract test: DARK_TOKENS / PLAYER_COLORS <-> assets/design-tokens.json.
 *
 * design-tokens.json is the machine-readable source of truth shared with the
 * desktop app (src/ui_constants.py has the mirror contract test in
 * tests/test_design_tokens_contract.py). If this fails, one side changed
 * without the other: update assets/design-tokens.json first, then both
 * consumers.
 */
import fs from 'fs';
import path from 'path';
import {DARK_TOKENS, PLAYER_COLORS} from './themes';

const TOKENS_PATH = path.join(
    __dirname, '..', '..', 'assets', 'design-tokens.json');

const snakeToCamel = (s) => s.replace(/_([a-z])/g, (_, c) => c.toUpperCase());

describe('design token contract (assets/design-tokens.json)', () => {
    const tokens = JSON.parse(fs.readFileSync(TOKENS_PATH, 'utf-8'));

    it('DARK_TOKENS matches palette.dark', () => {
        const expected = {};
        for (const [key, value] of Object.entries(tokens.palette.dark)) {
            expected[snakeToCamel(key)] = value;
        }
        expect(DARK_TOKENS).toEqual(expected);
    });

    it('PLAYER_COLORS matches player_colors', () => {
        expect(PLAYER_COLORS).toEqual(tokens.player_colors);
    });
});
