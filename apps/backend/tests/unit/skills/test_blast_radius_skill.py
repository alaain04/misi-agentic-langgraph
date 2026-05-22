from src.main_graph.skills.base import SkillContext
from src.main_graph.skills.blast_radius import BlastRadiusSkill


def _sbom_with_deps():
    return {
        "components": [
            {"name": "lodash", "version": "4.17.20"},
            {"name": "express", "version": "4.18.0"},
            {"name": "body-parser", "version": "1.20.0"},
        ],
        "dependencies": [
            {"ref": "express", "dependsOn": ["lodash", "body-parser"]},
            {"ref": "body-parser", "dependsOn": ["lodash"]},
        ],
    }


async def test_blast_radius_high_fanout():
    ctx = SkillContext(
        dep_name="lodash",
        hypothesis_id="h1",
        hypothesis="lodash has high blast radius",
        sbom=_sbom_with_deps(),
        concern="blast radius",
        services={},
    )
    skill = BlastRadiusSkill()
    evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.kind == "blast_radius_signal"
    assert "2" in ev.signal or "express" in ev.signal or "body-parser" in ev.signal


async def test_blast_radius_no_dependents():
    ctx = SkillContext(
        dep_name="some-leaf-dep",
        hypothesis_id="h1",
        hypothesis="some-leaf-dep has blast radius",
        sbom={"components": [], "dependencies": []},
        concern="blast radius",
        services={},
    )
    skill = BlastRadiusSkill()
    evidence = await skill.execute(ctx)

    assert len(evidence) == 1
    assert evidence[0].supports_hypothesis is False
