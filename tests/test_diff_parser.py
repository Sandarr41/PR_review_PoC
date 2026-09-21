from pr_review_agent.diff_parser import chunk_diff, detect_language, parse_diff, total_changed_lines


def test_parse_diff_splits_files(sample_diff_text):
    files = parse_diff(sample_diff_text)
    paths = [f.path for f in files]
    assert paths == ["user_service.py", "utils.py"]


def test_parse_diff_detects_language_and_new_file(sample_diff_text):
    files = parse_diff(sample_diff_text)
    utils = next(f for f in files if f.path == "utils.py")
    assert utils.language == "python"
    assert utils.is_new_file is True


def test_parse_diff_tracks_added_lines(sample_diff_text):
    files = parse_diff(sample_diff_text)
    user_service = next(f for f in files if f.path == "user_service.py")
    assert len(user_service.added_lines) == 7
    assert user_service.removed_lines == []


def test_detect_language_by_extension():
    assert detect_language("foo/bar.py") == "python"
    assert detect_language("foo/bar.unknownext") == "unknown"


def test_parse_diff_empty_input_returns_empty_list():
    assert parse_diff("") == []
    assert parse_diff("   \n  ") == []


def test_total_changed_lines(sample_diff_text):
    files = parse_diff(sample_diff_text)
    assert total_changed_lines(files) == sum(len(f.added_lines) + len(f.removed_lines) for f in files)


def test_chunk_diff_respects_size_limit(sample_diff_text):
    files = parse_diff(sample_diff_text)
    chunks = chunk_diff(files, chunk_size_lines=1)
    # each file has > 1 changed line, so each ends up in its own chunk
    assert len(chunks) == len(files)
    for chunk in chunks:
        assert len(chunk) == 1


def test_chunk_diff_single_chunk_when_under_limit(sample_diff_text):
    files = parse_diff(sample_diff_text)
    chunks = chunk_diff(files, chunk_size_lines=10_000)
    assert len(chunks) == 1
    assert len(chunks[0]) == len(files)


def test_chunk_diff_empty_input():
    assert chunk_diff([], chunk_size_lines=500) == []
