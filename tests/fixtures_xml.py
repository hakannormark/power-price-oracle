"""Trimmed ENTSO-E responses used by the parser tests. Structure matches the live API."""

NS = "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:0"

HOURLY = f"""<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="{NS}">
  <mRID>test-hourly</mRID>
  <createdDateTime>2026-09-02T11:01:00Z</createdDateTime>
  <TimeSeries>
    <mRID>1</mRID>
    <currency_Unit.name>EUR</currency_Unit.name>
    <price_Measure_Unit.name>MWH</price_Measure_Unit.name>
    <curveType>A01</curveType>
    <Period>
      <timeInterval>
        <start>2026-09-02T22:00Z</start>
        <end>2026-09-03T02:00Z</end>
      </timeInterval>
      <resolution>PT60M</resolution>
      <Point><position>1</position><price.amount>71.42</price.amount></Point>
      <Point><position>2</position><price.amount>68.10</price.amount></Point>
      <Point><position>3</position><price.amount>65.00</price.amount></Point>
      <Point><position>4</position><price.amount>63.25</price.amount></Point>
    </Period>
  </TimeSeries>
</Publication_MarketDocument>"""

# Curve type A03: a position is only listed when the price changes.
SPARSE = f"""<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="{NS}">
  <mRID>test-sparse</mRID>
  <createdDateTime>2026-09-02T11:01:00Z</createdDateTime>
  <TimeSeries>
    <mRID>1</mRID>
    <currency_Unit.name>EUR</currency_Unit.name>
    <curveType>A03</curveType>
    <Period>
      <timeInterval>
        <start>2026-09-02T22:00Z</start>
        <end>2026-09-03T02:00Z</end>
      </timeInterval>
      <resolution>PT60M</resolution>
      <Point><position>1</position><price.amount>40.00</price.amount></Point>
      <Point><position>3</position><price.amount>55.50</price.amount></Point>
    </Period>
  </TimeSeries>
</Publication_MarketDocument>"""

QUARTER_HOURLY = f"""<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="{NS}">
  <mRID>test-quarter</mRID>
  <createdDateTime>2026-09-02T11:01:00Z</createdDateTime>
  <TimeSeries>
    <mRID>1</mRID>
    <currency_Unit.name>EUR</currency_Unit.name>
    <curveType>A01</curveType>
    <Period>
      <timeInterval>
        <start>2026-09-02T22:00Z</start>
        <end>2026-09-02T23:00Z</end>
      </timeInterval>
      <resolution>PT15M</resolution>
      <Point><position>1</position><price.amount>10.00</price.amount></Point>
      <Point><position>2</position><price.amount>20.00</price.amount></Point>
      <Point><position>3</position><price.amount>30.00</price.amount></Point>
      <Point><position>4</position><price.amount>40.00</price.amount></Point>
    </Period>
  </TimeSeries>
</Publication_MarketDocument>"""

ACKNOWLEDGEMENT = """<?xml version="1.0" encoding="UTF-8"?>
<Acknowledgement_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-1:acknowledgementdocument:8:0">
  <mRID>ack</mRID>
  <Reason>
    <code>999</code>
    <text>No matching data found for Data item Day-ahead Prices [12.1.D].</text>
  </Reason>
</Acknowledgement_MarketDocument>"""
