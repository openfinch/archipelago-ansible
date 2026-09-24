"""Spoiler engine for the tracker: rebuilds the hosted seed with Archipelago's
own logic and answers "what has to happen before this item can be found?".

Uses the libraries bundled with the Archipelago release, so it must run on
the release's Python version (3.12 for 0.6.x).
"""
import importlib.abc
import importlib.machinery
import importlib.util
import logging
import os
import re
import sys
import tempfile
import zipfile


def bootstrap(archipelago_dir):
    """Make the release's bundled libraries importable, as its own executables do."""
    lib = os.path.join(archipelago_dir, "lib")

    class FlatExtensionFinder(importlib.abc.MetaPathFinder):
        # cx_Freeze stores extension modules flat, e.g. lib/bsdiff4.core.cpython-312-*.so
        def find_spec(self, fullname, path, target=None):
            for suffix in importlib.machinery.EXTENSION_SUFFIXES:
                file = os.path.join(lib, fullname + suffix)
                if os.path.exists(file):
                    loader = importlib.machinery.ExtensionFileLoader(fullname, file)
                    return importlib.util.spec_from_file_location(fullname, file, loader=loader)
            return None

    sys.meta_path.insert(0, FlatExtensionFinder())
    sys.path[:0] = [os.path.join(lib, "library.zip"), lib]
    # Behave like the frozen release: Utils.local_path() then resolves files from
    # the directory of sys.argv[0], and the source-checkout dependency updater is
    # skipped. Run from there too, as the release's own tools do.
    sys.frozen = True
    sys.executable = sys.argv[0] = os.path.join(archipelago_dir, "ArchipelagoGenerate")
    os.chdir(archipelago_dir)


class Spoilers:
    def __init__(self, seed_zip, players_dir):
        self.seed_zip = seed_zip
        self.players_dir = players_dir
        self.status = "loading"
        self.error = None
        self.mw = None
        self.by_id = {}  # (slot, location id) -> Location
        self.cache = {}  # results for the current set of checked locations
        self.cache_for = None

    def load(self):
        """Regenerate the seed and check it matches the hosted one. Blocking."""
        try:
            with zipfile.ZipFile(self.seed_zip) as z:
                name = next(n for n in z.namelist() if n.endswith("_Spoiler.txt"))
                spoiler = z.read(name).decode("utf-8-sig").replace("\r", "")
            seed = re.search(r"Seed: (\d+)", spoiler).group(1)

            import Generate
            import Main
            logging.disable(logging.WARNING)  # generation logs are noisy and not useful here
            with tempfile.TemporaryDirectory() as out:
                sys.argv = ["ArchipelagoGenerate", "--player_files_path", self.players_dir,
                            "--seed", seed, "--skip_output", "--outputpath", out]
                args, seed_value = Generate.main()
                mw = Main.main(args, seed_value)

            expected = {}
            locations = spoiler.split("\nLocations:\n")[1].split("\nPlaythrough:")[0]
            for line in locations.strip().splitlines():
                m = re.match(r"^(.*) \((\S+)\): (.*) \((\S+)\)$", line)
                if m:
                    expected[(m.group(1), m.group(2))] = (m.group(3), m.group(4))
            names = mw.player_name
            filled = [l for l in mw.get_filled_locations() if l.address is not None]
            mismatched = sum(expected.get((l.name, names[l.player])) != (l.item.name, names[l.item.player])
                             for l in filled)
            if mismatched:
                raise RuntimeError(f"regenerated seed differs from the hosted one at {mismatched} locations "
                                   "(player files changed since generation, or seed made elsewhere)")
            self.mw = mw
            self.by_id = {(l.player, l.address): l for l in filled}
            self.status = "ready"
        except Exception as e:
            self.status = "error"
            self.error = f"{type(e).__name__}: {e}"
            logging.disable(logging.NOTSET)
            logging.exception("spoiler engine failed to load")

    def describe(self, loc, checked):
        names = self.mw.player_name
        return {
            "slot": loc.player, "location_id": loc.address,
            "location": loc.name, "world": names[loc.player],
            "item": loc.item.name, "owner": names[loc.item.player],
            "flags": int(loc.item.classification), "checked": (loc.player, loc.address) in checked,
        }

    def search(self, query, checked, limit=50):
        q = query.strip().lower()
        if not q:
            return []
        hits = [l for l in self.by_id.values() if q in l.item.name.lower() or q in l.name.lower()]
        hits.sort(key=lambda l: ((l.player, l.address) in checked, not l.item.advancement, l.item.name, l.name))
        return [self.describe(l, checked) for l in hits[:limit]]

    def path(self, slot, location_id, checked):
        """Rounds of still-unchecked locations that must be done before the target, in order."""
        target = self.by_id[(slot, location_id)]
        checked = frozenset(checked)
        if checked != self.cache_for:
            self.cache, self.cache_for = {}, checked
        if target not in self.cache:
            self.cache[target] = self._path(target, checked)
        return self.cache[target]

    def _path(self, target, checked):
        from BaseClasses import CollectionState

        result = {"target": self.describe(target, checked), "rounds": [], "reachable": True}
        if (target.player, target.address) in checked:
            return result
        base = CollectionState(self.mw)
        done = set()
        for key in checked:
            loc = self.by_id.get(key)
            if loc:
                done.add(loc)
                if loc.item.advancement:
                    base.collect(loc.item, True, loc)
        candidates = [l for l in self.mw.get_filled_locations()
                      if l.item.advancement and l not in done and l is not target]

        def rounds_with(allowed):
            state = base.copy()
            pending = list(allowed)
            rounds = []
            while not target.can_reach(state):
                sphere = [l for l in pending if l.can_reach(state)]
                if not sphere:
                    return None
                for l in sphere:
                    state.collect(l.item, True, l)
                    pending.remove(l)
                rounds.append(sphere)
            return rounds

        rounds = rounds_with(candidates)
        if rounds is None:
            result["reachable"] = False
            return result
        # Drop every check the target can be reached without, latest first.
        # Events (no address) are free logic flags, so they always stay.
        keep = [l for r in rounds for l in r]
        for loc in reversed(keep[:]):
            if loc.address is None:
                continue
            trial = [l for l in keep if l is not loc]
            if rounds_with(trial) is not None:
                keep = trial
        rounds = rounds_with(keep) or []
        shown = [[self.describe(l, checked) for l in r if l.address is not None] for r in rounds]
        result["rounds"] = [r for r in shown if r]
        return result
