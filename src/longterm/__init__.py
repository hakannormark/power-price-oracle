"""Long-term forecast: the calendar-month mean price per zone, one to three months out.

Hourly shape is not forecastable that far ahead, and nobody plans around it;
what a household or a buyer can use is the level of next month and the one
after. So this package forecasts monthly means, with a band, and scores them
the same way the hourly forecast is scored: only on information that existed
when the forecast was issued.
"""
