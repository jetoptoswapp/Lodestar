"""Persistence 層：唯一碰 DB 的地方（host owns all I/O）。
plugin 永遠拿不到 connection；所有 DB 寫入經由 host/DAL。"""
