from pydantic import BaseModel, ConfigDict


class BybitLeverageFilter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    minLeverage: str
    maxLeverage: str
    leverageStep: str


class BybitPriceFilter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    minPrice: str
    maxPrice: str
    tickSize: str


class BybitLotSizeFilter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    minNotionalValue: str
    maxOrderQty: str
    maxMktOrderQty: str
    minOrderQty: str
    qtyStep: str


class BybitInstrument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str
    contractType: str
    status: str
    baseCoin: str
    quoteCoin: str
    launchTime: str
    leverageFilter: BybitLeverageFilter
    priceFilter: BybitPriceFilter
    lotSizeFilter: BybitLotSizeFilter
    settleCoin: str
    fundingInterval: int


class BybitInstrumentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str
    list: list[BybitInstrument]
    nextPageCursor: str = ""


class BybitInstrumentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    retCode: int
    retMsg: str
    result: BybitInstrumentResult
    time: int


class BybitLinearTicker(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str
    lastPrice: str
    markPrice: str
    openInterest: str
    openInterestValue: str
    turnover24h: str
    volume24h: str
    fundingRate: str
    bid1Price: str
    ask1Price: str


class BybitTickerResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str
    list: list[BybitLinearTicker]


class BybitTickerResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    retCode: int
    retMsg: str
    result: BybitTickerResult
    time: int


class BybitKlineResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str
    symbol: str
    list: list[list[str]]


class BybitKlineResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    retCode: int
    retMsg: str
    result: BybitKlineResult
    time: int
