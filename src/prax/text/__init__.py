"""The shapes of text prax writes and reads: the markup it emits, the
chunks it cuts, what a region of a page is (a reference, an ad, an
ingredient list), the language a text is in, and what a model wrapped
its answer in. Mostly pure functions over strings; they import little of
prax and nothing of the store.
"""
