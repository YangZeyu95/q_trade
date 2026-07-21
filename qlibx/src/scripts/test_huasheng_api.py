import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from huasheng_api import HuashengGatewayAPI


class TestHuashengGatewayAPI(unittest.TestCase):
    def setUp(self):
        self.api = HuashengGatewayAPI('http://gateway.test/')

    @patch('huasheng_api.requests.post')
    def test_post_request_success(self, mock_post):
        response = Mock()
        response.json.return_value = {'ok': True, 'data': {'value': 1}}
        mock_post.return_value = response

        result = self.api._post_request('trade/Test', {'symbol': 'TQQQ'})

        self.assertEqual(result, {'value': 1})
        self.assertIsNone(self.api.last_error)
        mock_post.assert_called_once_with(
            'http://gateway.test/trade/Test',
            json={'timeout_sec': 10, 'params': {'symbol': 'TQQQ'}},
            timeout=10,
        )

    @patch('huasheng_api.requests.post')
    def test_post_request_api_error_and_transport_error_return_none(self, mock_post):
        response = Mock()
        response.json.return_value = {'ok': False, 'err': 'bad request'}
        mock_post.return_value = response
        self.assertIsNone(self.api._post_request('test', {}))
        self.assertEqual(self.api.last_error, 'test: bad request')

        mock_post.side_effect = TimeoutError('gateway timeout')
        self.assertIsNone(self.api._post_request('test', {}))
        self.assertIn('test: TimeoutError: gateway timeout', self.api.last_error)

    def test_login_requires_password_and_encrypts_it(self):
        self.assertFalse(self.api.log_in(''))
        with patch.object(self.api, '_encrypt_password', return_value='encrypted') as encrypt, \
                patch.object(self.api, '_post_request', return_value={'ok': True}) as post:
            self.assertTrue(self.api.log_in('secret'))
        encrypt.assert_called_once_with('secret')
        post.assert_called_once_with('trade/TradeLogin', {'password': 'encrypted'})

    def test_login_failure_returns_false(self):
        with patch.object(self.api, '_post_request', return_value=None):
            self.assertFalse(self.api.log_in('secret'))

    def test_quote_falls_back_between_us_stock_and_etf_types(self):
        with patch.object(self.api, '_post_request', side_effect=[None, {'basicQot': [{'lastPrice': 70}]}]) as post:
            quote = self.api.get_realtime_quote('US.TQQQ', 20002)

        self.assertEqual(quote, {'lastPrice': 70})
        self.assertEqual(post.call_args_list[0].args[1]['security'][0], {'dataType': 20002, 'code': 'TQQQ'})
        self.assertEqual(post.call_args_list[1].args[1]['security'][0], {'dataType': 20000, 'code': 'TQQQ'})
        self.assertEqual(post.call_args_list[0].args[1]['mktTmType'], 0)

    def test_batch_quotes_uses_one_gateway_request_and_normalizes_codes(self):
        with patch.object(self.api, '_post_request', return_value={
            'basicQot': [
                {'security': {'code': 'TQQQ'}, 'lastPrice': 70},
                {'security': {'code': 'AAPL'}, 'lastPrice': 200},
            ]
        }) as post:
            quotes = self.api.get_realtime_quotes(
                ['US.TQQQ', 'AAPL'], data_type=20002
            )

        self.assertEqual(quotes['TQQQ']['lastPrice'], 70)
        self.assertEqual(quotes['AAPL']['lastPrice'], 200)
        post.assert_called_once_with('hq/BasicQot', {
            'security': [
                {'dataType': 20002, 'code': 'TQQQ'},
                {'dataType': 20002, 'code': 'AAPL'},
            ],
            'mktTmType': 0,
        })

    def test_position_normalizes_symbol_and_uses_sellable_quantity(self):
        with patch.object(self.api, '_post_request', return_value={
            'positionList': [{'stockCode': 'TQQQ.US', 'canSellAmount': '12'}]
        }):
            self.assertEqual(self.api.get_stock_position_qty('US.TQQQ'), 12)

    def test_invalid_position_quantity_fails_closed(self):
        for quantity in ('not-a-number', 'nan', '-2', '1.5'):
            with self.subTest(quantity=quantity), patch.object(
                self.api,
                '_post_request',
                return_value={
                    'positionList': [
                        {'stockCode': 'TQQQ', 'canSellAmount': quantity}
                    ]
                },
            ):
                self.assertEqual(self.api.get_stock_position_qty('TQQQ'), 0)

    def test_position_supports_holds_list_alias(self):
        with patch.object(self.api, '_post_request', return_value={
            'holdsList': [{'stockCode': 'AAPL', 'marketValue': '100'}]
        }):
            self.assertEqual(self.api.get_position()['positionList'][0]['stockCode'], 'AAPL')

    def test_total_portfolio_value_is_absolute_and_returns_none_on_bad_response(self):
        with patch.object(self.api, 'get_position', return_value={
            'positionList': [{'marketValue': '-100'}, {'marketValue': '250'}]
        }):
            self.assertEqual(self.api.get_total_portfolio_value(), 350.0)
        with patch.object(self.api, 'get_position', return_value=None):
            self.assertIsNone(self.api.get_total_portfolio_value())

    def test_check_connection_is_read_only(self):
        with patch.object(self.api, '_post_request', return_value={'assetBalance': '1000'}) as post:
            self.assertTrue(self.api.check_connection('P'))
        post.assert_called_once_with(
            'trade/TradeQueryMarginFundInfo',
            {'exchangeType': 'P'},
        )

        with patch.object(self.api, '_post_request', return_value=None):
            self.assertFalse(self.api.check_connection('P'))

    def test_account_and_order_methods_forward_exchange_and_payload(self):
        with patch.object(self.api, '_post_request', return_value={'assetBalance': '1000'}) as post:
            self.assertEqual(self.api.get_account_funds('P'), {'assetBalance': '1000'})
        post.assert_called_once_with('trade/TradeQueryMarginFundInfo', {'exchangeType': 'P'})

        with patch.object(self.api, '_post_request', return_value={'orderId': 'O1'}) as post:
            result = self.api.place_order('P', 'TQQQ', 10, '70.01', '1', '3')
        self.assertEqual(result, {'orderId': 'O1'})
        post.assert_called_once_with('trade/TradeEntrust', {
            'exchangeType': 'P',
            'stockCode': 'TQQQ',
            'entrustAmount': 10,
            'entrustPrice': '70.01',
            'entrustBs': '1',
            'entrustType': '3',
        })

        with patch.object(self.api, '_post_request', return_value={
            'data': [{
                'entrustId': 'O1',
                'status': '8',
                'statusDesc': '已成',
            }],
        }) as post:
            details = self.api.get_order_details('O1')
        self.assertEqual(details['orderStatus'], '8')
        self.assertEqual(details['orderStatusName'], '8')
        post.assert_called_once_with(
            'trade/TradeQueryRealEntrustList',
            {'exchangeType': 'P', 'entrustId': ['O1']},
        )

    def test_order_query_returns_none_when_requested_order_is_not_present(self):
        with patch.object(self.api, '_post_request', return_value={
            'data': [{'entrustId': 'OTHER', 'status': '2'}],
        }):
            self.assertIsNone(self.api.get_order_details('O1'))

    def test_extract_order_id_handles_gateway_response_wrappers(self):
        self.assertEqual(HuashengGatewayAPI.extract_order_id({'orderId': 'O1'}), 'O1')
        self.assertEqual(HuashengGatewayAPI.extract_order_id({'data': 'O2'}), 'O2')
        self.assertEqual(
            HuashengGatewayAPI.extract_order_id({'data': {'data': 'O3'}}),
            'O3',
        )
        self.assertEqual(
            HuashengGatewayAPI.extract_order_id({'result': {'entrustNo': 123}}),
            '123',
        )
        self.assertEqual(HuashengGatewayAPI.extract_order_id(None), '')

    def test_cancel_order_forwards_original_order_parameters(self):
        with patch.object(self.api, '_post_request', return_value={'success': True}) as post:
            result = self.api.cancel_order(
                order_id='O1',
                stock_code='TQQQ',
                exchange_type='P',
                entrust_amount=10,
                entrust_price='70.01',
                entrust_type='3',
            )

        self.assertEqual(result, {'success': True})
        post.assert_called_once_with('trade/TradeCancelEntrust', {
            'exchangeType': 'P',
            'stockCode': 'TQQQ',
            'entrustId': 'O1',
            'entrustAmount': '10',
            'entrustPrice': '70.01',
            'entrustType': '3',
        })

    @patch.dict(os.environ, {}, clear=True)
    def test_fear_greed_requires_auth_key(self):
        self.assertIsNone(self.api.fetch_fear_greed_index('TQQQ', '3', 'us'))

    @patch.dict(os.environ, {'SZDT_AUTH_KEY': 'key'})
    @patch('huasheng_api.requests.post')
    def test_fear_greed_success_normalizes_us_symbol(self, mock_post):
        response = Mock(status_code=200)
        response.json.return_value = {'status': 1, 'data': {'score': '-42.5'}}
        mock_post.return_value = response

        self.assertEqual(self.api.fetch_fear_greed_index('US.TQQQ', '3', 'us'), -42.5)
        mock_post.assert_called_once_with(
            'https://szdt.tech/api/partner/invest/stock/scan',
            headers={'X-Auth': 'key'},
            data={'code': 'US.TQQQ', 'lever': '3', 'emo_area': 'us'},
            timeout=10,
        )


if __name__ == '__main__':
    unittest.main()
