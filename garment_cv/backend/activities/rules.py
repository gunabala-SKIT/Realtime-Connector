"""The activity rules themselves (spec section 11): what objects, in what zone, with what
movement, sustained for how long, add up to which activity. Deliberately data, not a pile of
if-statements - the spec's own words are "the rule engine must be configurable rather than
hard-coded wherever possible" - so a rule is one `ActivityRule` entry, and adding a new one (or
changing what it requires) never touches `engine.py`'s logic.

Which rules can actually fire right now, given the COCO stand-in (see `detection/coco_standin.py`):

  CUTTING          person + scissors + a "cutting" zone + scissors movement  -> CAN fire. This
                   drops the spec's own "Fabric detected" condition (spec section 3), because
                   fabric has no COCO stand-in at all - disclosed here, not silently dropped.
                   Movement is required because spec section 3 is explicit that a static scissors
                   detection alone must never be treated as cutting.
  MARKING          needs `marking_tool`  -> never fires; that class doesn't exist in this model.
  STITCHING        needs `sewing_machine` -> never fires; that class doesn't exist in this model.
  PIECE_HANDLING   needs `fabric_piece`   -> never fires; that class doesn't exist in this model.

MARKING/STITCHING/PIECE_HANDLING are defined in full below anyway - not stubbed out - so the
engine, its configuration, its per-rule thresholds and the zone-activity picker on the `/zones`
page are all already correct and ready the day a real garment-trained model adds those classes;
nothing about the rule engine itself would need to change, only the detector's model file.
"""
from __future__ import annotations

from dataclasses import dataclass

IDLE = "IDLE"


@dataclass(frozen=True)
class ActivityRule:
    name: str
    zone_activity: str              # matches a Zone's `activity` field (zones/store.py)
    required_classes: frozenset[str]  # detected classes that must be attributed to the person
    requires_movement: bool          # if True, the attributed object must show real movement,
                                     # not just be present (spec section 3's "holding ≠ using")


RULES: tuple[ActivityRule, ...] = (
    ActivityRule("CUTTING", "cutting", frozenset({"scissors"}), requires_movement=True),
    ActivityRule("MARKING", "marking", frozenset({"marking_tool"}), requires_movement=True),
    ActivityRule("STITCHING", "stitching", frozenset({"sewing_machine"}), requires_movement=False),
    ActivityRule("PIECE_HANDLING", "piece_handling", frozenset({"fabric_piece"}), requires_movement=False),
)

# Rules whose required class the current COCO stand-in can ever actually produce (see the module
# docstring). Everything else is real, tested code that is simply never reachable yet - the API
# and the status page both say so explicitly, the same way Phase 2 discloses undetectable classes.
REACHABLE_NOW = frozenset({"CUTTING"})
