-- Acronyms the library itself defines ("antiderivative antialiasing (ADAA)"),
-- collected from the text artifacts by scripts/build_acronyms.py and used by
-- store.search to expand a query token on both the keyword and the vector
-- side. Rebuilt in full by the script; never edited by hand.
CREATE TABLE acronyms (
    acronym   TEXT NOT NULL,   -- lowercased, as typed in a query
    expansion TEXT NOT NULL,   -- lowercased phrase, words separated by single spaces
    docs      INTEGER NOT NULL, -- documents that define it this way
    PRIMARY KEY (acronym, expansion)
);
