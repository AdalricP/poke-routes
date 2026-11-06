from __future__ import annotations

import pytest

from pokeroutes.region import edit, network

SEEDS_THAT_USED_TO_LEAVE_A_STUCK_LINK = (1, 4, 12)


@pytest.mark.parametrize("seed", range(16))
def test_generate_map_leaves_no_unintended_dead_end(seed):
    generated_document = edit.generate_map(edit.generate_doc(seed))

    dead_end_names = [route["name"] for route in network.unintended_dead_end_routes(generated_document["routes"])]

    assert dead_end_names == []


@pytest.mark.parametrize("seed", SEEDS_THAT_USED_TO_LEAVE_A_STUCK_LINK)
def test_a_stuck_link_is_taken_back_and_laid_another_way(seed):
    generated_document = edit.generate_map(edit.generate_doc(seed))

    unlaid_link_names = [link["name"] for link in generated_document["links"] if not link.get("routes")]

    assert unlaid_link_names == []


def test_drawn_and_landmark_dead_ends_count_as_intended():
    drawn_dead_end = dict(name="Route 1", exits=[dict(side="east")], kind_link="drawn")
    landmark_dead_end = dict(name="Route 2", exits=[dict(side="east")], goal=[4, 4])
    stuck_stub = dict(name="Route 3", exits=[dict(side="east")], kind_link="main")

    dead_ends = network.unintended_dead_end_routes([drawn_dead_end, landmark_dead_end, stuck_stub])

    assert [route["name"] for route in dead_ends] == ["Route 3"]
