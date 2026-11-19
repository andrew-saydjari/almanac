"""`almanac add metadata --extended` (value-added columns, adapted from upstream a922dee).

No database: fake query functions, as in test_metadata_build.py.
"""

import h5py as h5
import numpy as np
import pytest
from click.testing import CliRunner

import almanac.catalog as catalog
import almanac.metadata as md
from almanac.cli import main
from almanac.data_models.source import EXTENDED_COLUMN_GROUPS, ExtendedSource, Source

from tests.test_metadata_build import (
    IDS, NOT_FOUND, Recorder, assert_same_meta, build, fake_query, fake_row, make_almanac, read_meta,
)

BASE_COLUMNS = list({**Source.model_fields, **Source.model_computed_fields})
EXT_COLUMNS = [n for names in EXTENDED_COLUMN_GROUPS.values() for n in names]


def fake_ext_row(sdss_id):
    """What `_query_catalog_once(..., extended=True)` returns: base row + value-added keys."""
    row = fake_row(sdss_id)
    row.update(
        ra=float(sdss_id), dec=-float(sdss_id) / 10, l=1.0, b=2.0, healpix=sdss_id * 3,
        n_associated=1,
        w1_flux=100.0, w1_mag=17.5, w1uflags=4,
        zgr_teff=4800.0, r_med_geo=1234.5, bailer_jones_flags="10",
        u_jkc_mag=12.0, u_jkc_mag_flag=1, c_star=0.01,
        sdss4_apogee_target1_flags=2**31 + 5,
        # mag4_5 etc. left out: an unmatched GLIMPSE source keeps its defaults
    )
    return row


def fake_ext_query(ids, suffix=""):
    return {i: fake_ext_row(i) for i in ids if i not in NOT_FOUND}


class ExtRecorder(Recorder):
    def __call__(self, ids, suffix=""):
        self.calls.append(list(ids))
        return fake_ext_query(ids, suffix)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


class TestModel:
    def test_source_unchanged_and_extended_appends(self):
        ext = list({**ExtendedSource.model_fields, **ExtendedSource.model_computed_fields})
        assert len(BASE_COLUMNS) == 89
        assert ext[: len(BASE_COLUMNS)] == BASE_COLUMNS
        assert ext[len(BASE_COLUMNS):] == EXT_COLUMNS
        assert len(EXT_COLUMNS) == 79

    def test_no_never_filled_reddening_columns(self):
        assert not [n for n in EXT_COLUMNS if "ebv" in n]

    def test_none_becomes_default(self):
        s = ExtendedSource(sdss_id=1, catalogid=2, version_id=3, lead="x",
                           ra=None, healpix=None, bailer_jones_flags=None)
        assert np.isnan(s.ra) and s.healpix == -1 and s.bailer_jones_flags == ""


# --------------------------------------------------------------------------- #
# Upstream helpers (catalog.py, from a922dee)
# --------------------------------------------------------------------------- #


class TestDerivedQuantities:
    def test_unwise_mag(self):
        mag, e_mag = catalog.unwise_flux_to_mag([100.0, 0.0, np.nan], [1.0, 1.0, 1.0], "w1")
        assert mag[0] == pytest.approx(22.5 - 5.0 - 4e-3)
        assert e_mag[0] == pytest.approx(2.5 / np.log(10) / 100)
        assert np.isnan(mag[1:]).all() and np.isnan(e_mag[1:]).all()

    def test_unsigned_int32(self):
        assert catalog.to_unsigned_int32(-1) == 2**32 - 1
        assert catalog.to_unsigned_int32(5) == 5
        assert catalog.to_unsigned_int32(None) is None

    def test_add_derived_quantities(self):
        pytest.importorskip("healpy")
        meta = {
            1: dict(ra=None, dec=None, gaia_ra=266.405, gaia_dec=-28.936, w1_flux=100.0, w1_dflux=1.0,
                    zgr_teff=4.8, sdss4_apogee_target1_flags=-2**31),
            2: dict(ra=10.0, dec=20.0, gaia_ra=None, gaia_dec=None),
            3: dict(ra=None, dec=None, gaia_ra=None, gaia_dec=None),
        }
        catalog.add_derived_quantities(meta)
        assert meta[1]["ra"] == 266.405  # falls back to Gaia DR3
        assert abs(meta[1]["b"]) < 0.1 and (meta[1]["l"] < 0.1 or meta[1]["l"] > 359.9)  # Galactic centre
        assert meta[1]["zgr_teff"] == pytest.approx(4800.0)  # kK -> K
        assert meta[1]["sdss4_apogee_target1_flags"] == 2**31
        assert meta[1]["healpix"] >= 0 and meta[2]["healpix"] >= 0
        assert meta[3]["healpix"] == -1 and np.isnan(meta[3]["l"])
        assert np.isnan(meta[2]["w1_mag"])

    def test_merge_missing_keeps_existing(self):
        d = {"a": 1, "b": None}
        catalog.merge_missing(d, {"a": 2, "b": 3, "c": 4})
        assert d == {"a": 1, "b": 3, "c": 4}


class TestValueAddedQueriesAreStrict:
    def test_missing_sdssdb_model_raises_not_skips(self):
        class FakeDB:
            rolled_back = False

            def rollback(self):
                FakeDB.rolled_back = True

        class FakeCdb:  # no catalogdb models at all
            database = FakeDB()

        class FakeSource:
            sdss_id = None

            @classmethod
            def select(cls, *a):
                return cls

        with pytest.raises(RuntimeError, match="catalogdb"):
            catalog._merge_value_added_catalogs(FakeSource, FakeCdb, {1: {}})

    def test_database_error_rolls_back_and_raises(self, monkeypatch):
        calls = []

        class Boom(Exception):
            pass

        class FakeQuery:
            def dicts(self):
                return self

            def iterator(self):
                raise Boom("relation does not exist")

        class FakeDB:
            def rollback(self):
                calls.append("rollback")

        class FakeCdb:
            database = FakeDB()

        monkeypatch.setattr(catalog, "VALUE_ADDED_SELECTS", (("x", lambda S, c: FakeQuery()),))
        with pytest.raises(Boom):
            catalog._merge_value_added_catalogs(object, FakeCdb, {1: {}})
        assert calls == ["rollback"]

    def test_query_batch_functions_pass_the_mode(self, monkeypatch):
        seen = []
        monkeypatch.setattr(catalog, "_query_catalog",
                            lambda ids, suffix="", extended=False: seen.append(extended) or {})
        md._default_query_batch([1], "_0")
        md._extended_query_batch([1], "_0")
        assert seen == [False, True]


# --------------------------------------------------------------------------- #
# Through the resumable build (PR #14 machinery)
# --------------------------------------------------------------------------- #


class TestBuild:
    def test_default_mode_is_the_89_columns(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        build(tmp_path, fake_ext_query, input_path=inp, output_path=str(tmp_path / "t.h5"))
        cols, attrs, _ = read_meta(str(tmp_path / "t.h5"))
        assert list(cols) == BASE_COLUMNS
        assert "extended" not in attrs and "extended_column_groups" not in attrs

    def test_extended_is_base_plus_new_columns(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        build(tmp_path, fake_query, input_path=inp, output_path=str(tmp_path / "base.h5"))
        build(tmp_path, fake_ext_query, input_path=inp, output_path=str(tmp_path / "ext.h5"), extended=True)
        base, _, nf_b = read_meta(str(tmp_path / "base.h5"))
        ext, attrs, nf_e = read_meta(str(tmp_path / "ext.h5"))
        assert list(ext) == BASE_COLUMNS + EXT_COLUMNS
        assert_same_meta(base, {k: ext[k] for k in BASE_COLUMNS})
        np.testing.assert_array_equal(nf_b, nf_e)
        assert attrs["extended"]
        assert [g.decode() if isinstance(g, bytes) else g for g in attrs["extended_column_groups"]] == list(
            EXTENDED_COLUMN_GROUPS
        )
        ids = ext["sdss_id"]
        np.testing.assert_allclose(ext["ra"], ids.astype(float))
        assert (ext["sdss4_apogee_target1_flags"] == 2**31 + 5).all()
        assert np.isnan(ext["mag4_5"]).all() and (ext["sqf_4_5"] == 0).all()  # unmatched -> defaults
        assert {v.decode() for v in ext["bailer_jones_flags"]} == {"10"}

    def test_extended_pool_matches_inline(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        build(tmp_path, fake_ext_query, input_path=inp, output_path=str(tmp_path / "a1.h5"), extended=True)
        build(tmp_path, fake_ext_query, input_path=inp, output_path=str(tmp_path / "a2.h5"), extended=True,
              query_workers=2)
        assert_same_meta(read_meta(str(tmp_path / "a1.h5"))[0], read_meta(str(tmp_path / "a2.h5"))[0])

    def test_checkpoint_mode_mismatch_is_refused(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        ckpt = str(tmp_path / "ckpt")
        build(tmp_path, fake_query, input_path=inp, output_path=str(tmp_path / "t.h5"),
              checkpoint_dir=ckpt, keep_checkpoint=True)
        with pytest.raises(md.MetadataBuildError, match="--extended"):
            build(tmp_path, fake_ext_query, input_path=inp, output_path=str(tmp_path / "t.h5"),
                  checkpoint_dir=ckpt, extended=True)
        # and the other way round
        ckpt2 = str(tmp_path / "ckpt2")
        build(tmp_path, fake_ext_query, input_path=inp, output_path=str(tmp_path / "e.h5"),
              checkpoint_dir=ckpt2, keep_checkpoint=True, extended=True)
        with pytest.raises(md.MetadataBuildError, match="--extended"):
            build(tmp_path, fake_query, input_path=inp, output_path=str(tmp_path / "e.h5"),
                  checkpoint_dir=ckpt2)
        # an existing (pre-option) checkpoint has no "extended" key: it resumes as default
        build(tmp_path, fake_query, input_path=inp, output_path=str(tmp_path / "t.h5"), checkpoint_dir=ckpt)

    def test_resume_extended(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        ckpt = str(tmp_path / "ckpt")
        calls = []

        def flaky(ids, suffix=""):
            calls.append(list(ids))
            if len(calls) > 3:
                raise ValueError("boom")
            return fake_ext_query(ids, suffix)

        with pytest.raises(md.MetadataBuildError):
            build(tmp_path, flaky, input_path=inp, output_path=str(tmp_path / "t.h5"),
                  checkpoint_dir=ckpt, extended=True, max_batch_attempts=1)
        rec2 = ExtRecorder()
        build(tmp_path, rec2, input_path=inp, output_path=str(tmp_path / "t.h5"),
              checkpoint_dir=ckpt, extended=True)
        assert len(rec2.queried) == len(IDS) - 3 * 8  # the 3 finished batches were not redone
        build(tmp_path, fake_ext_query, input_path=inp, output_path=str(tmp_path / "ref.h5"), extended=True)
        assert_same_meta(read_meta(str(tmp_path / "t.h5"))[0], read_meta(str(tmp_path / "ref.h5"))[0])

    def test_since_without_extended_columns_requeries(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        prev = str(tmp_path / "prev.h5")
        build(tmp_path, fake_query, input_path=inp, output_path=prev)  # default columns only
        rec = ExtRecorder()
        build(tmp_path, rec, input_path=inp, output_path=str(tmp_path / "e.h5"), since=prev, extended=True)
        # every found id is queried again; known not-found ids are still skipped
        assert rec.queried == sorted(set(IDS) - NOT_FOUND)
        build(tmp_path, fake_ext_query, input_path=inp, output_path=str(tmp_path / "ref.h5"), extended=True)
        assert_same_meta(read_meta(str(tmp_path / "e.h5"))[0], read_meta(str(tmp_path / "ref.h5"))[0])

    def test_since_with_extended_columns_copies(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        prev = str(tmp_path / "prev.h5")
        build(tmp_path, fake_ext_query, input_path=inp, output_path=prev, extended=True)
        rec = ExtRecorder()
        build(tmp_path, rec, input_path=inp, output_path=str(tmp_path / "e.h5"), since=prev, extended=True)
        assert rec.queried == []
        assert_same_meta(read_meta(prev)[0], read_meta(str(tmp_path / "e.h5"))[0])

    def test_default_since_an_extended_file_drops_the_new_columns(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        prev = str(tmp_path / "prev.h5")
        build(tmp_path, fake_ext_query, input_path=inp, output_path=prev, extended=True)
        build(tmp_path, Recorder(), input_path=inp, output_path=str(tmp_path / "d.h5"), since=prev)
        build(tmp_path, fake_query, input_path=inp, output_path=str(tmp_path / "ref.h5"))
        d = read_meta(str(tmp_path / "d.h5"))[0]
        assert list(d) == BASE_COLUMNS
        assert_same_meta(d, read_meta(str(tmp_path / "ref.h5"))[0])

    def test_missing_healpy_fails_early(self, tmp_path, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_healpy(name, *a, **k):
            if name == "healpy":
                raise ImportError("No module named 'healpy'")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_healpy)
        inp = make_almanac(str(tmp_path / "a.h5"))
        with pytest.raises(md.MetadataBuildError, match="healpy"):
            build(tmp_path, fake_ext_query, input_path=inp, output_path=str(tmp_path / "e.h5"), extended=True)
        assert not (tmp_path / "e.h5.meta-checkpoint").exists()


class TestCli:
    def test_cli_extended_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(md, "_default_query_batch", fake_query)
        monkeypatch.setattr(md, "_extended_query_batch", fake_ext_query)
        inp = make_almanac(str(tmp_path / "a.h5"))
        runner = CliRunner()
        for name, extra in (("d.h5", []), ("e.h5", ["--extended"])):
            r = runner.invoke(main, ["add", "metadata", inp, "--output", str(tmp_path / name),
                                     "--query-workers", "0", *extra], catch_exceptions=False)
            assert r.exit_code == 0, r.output
        assert list(read_meta(str(tmp_path / "d.h5"))[0]) == BASE_COLUMNS
        assert list(read_meta(str(tmp_path / "e.h5"))[0]) == BASE_COLUMNS + EXT_COLUMNS

    def test_help_mentions_extended(self):
        r = CliRunner().invoke(main, ["add", "metadata", "--help"])
        assert "--extended" in r.output

    def test_pgdump_extended_tables_only_on_request(self, tmp_path, monkeypatch):
        import subprocess

        dumped = []

        class R:
            returncode = 0

        def fake_run(cmd, **k):
            dumped.append(cmd[cmd.index("-t") + 1])
            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        from almanac.cli import PGDUMP_EXTENDED_TABLES, PGDUMP_TABLES

        runner = CliRunner()
        r = runner.invoke(main, ["pgdump", str(tmp_path / "d"), "--host", "h", "--user", "u"])
        assert r.exit_code == 0, r.output
        assert dumped == list(PGDUMP_TABLES)
        dumped.clear()
        r = runner.invoke(main, ["pgdump", str(tmp_path / "d"), "--host", "h", "--user", "u", "--extended"])
        assert r.exit_code == 0, r.output
        assert dumped == list(PGDUMP_TABLES) + list(PGDUMP_EXTENDED_TABLES)
